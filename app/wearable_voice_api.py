"""Feature-gated, authenticated, source-bound wearable push-to-talk HTTP API.

Do not expose this API over plain internet HTTP. It accepts only the existing
revocable VP3 paired-app bearer token, never the HomeServer owner secret.
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .services import wearable_voice_gateway as gateway

router = APIRouter(prefix="/api/v1/wearable/audio", tags=["wearable-audio"])

_LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class BeginTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=80)
    intent: Literal["press"]


def _guard_transport(request: Request) -> None:
    # Trust only the actual ASGI scheme set by a *trusted* reverse proxy.
    # Never infer TLS from arbitrary X-Forwarded-Proto request headers.
    host = request.client.host if request.client else ""
    local_dev_override = (
        os.environ.get("VP3_WEARABLE_AUDIO_ALLOW_LOCAL_HTTP", "") == "1"
        and host in _LOCAL_CLIENTS
    )
    if request.url.scheme != "https" and not local_dev_override:
        raise HTTPException(status_code=426, detail="Wearable audio requires HTTPS.")


def _bearer(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
        raise HTTPException(status_code=401, detail="Wearable bearer token required.")
    return authorization[7:]


def _device(request: Request, expected_device: str | None = None) -> tuple[dict, str]:
    token = _bearer(request)
    try:
        paired = gateway.authenticate_device(token, expected_device)
    except gateway.WearableVoiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return paired, token


def _ready(request: Request) -> None:
    if not gateway.enabled():
        raise HTTPException(status_code=503, detail="Wearable audio gateway is disabled.")
    _guard_transport(request)


def _error(exc: gateway.WearableVoiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/start")
async def begin_turn(payload: BeginTurn, request: Request):
    _ready(request)
    paired, _ = _device(request, payload.device_id)
    try:
        turn = gateway.registry.begin(str(paired["app_key"]))
    except gateway.WearableVoiceError as exc:
        raise _error(exc) from exc
    return {
        "protocol": gateway.VERSION,
        "turn_id": turn.id,
        "device_id": turn.device_id,
        "expires_in_seconds": gateway.MAX_SECONDS,
        "audio_format": "wav-pcm16-mono-16000",
    }


async def _limited_audio(request: Request) -> bytes:
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > gateway.MAX_WAV_BYTES:
            raise gateway.WearableVoiceError("Wearable audio turn exceeded size limit.", 413)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/finish/{turn_id}")
async def finish_turn(turn_id: str, request: Request):
    _ready(request)
    paired, token = _device(request)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() not in {
        "audio/wav", "audio/x-wav"
    }:
        raise HTTPException(status_code=415, detail="PCM WAV audio required.")
    try:
        turn = gateway.registry.consume(str(paired["app_key"]), turn_id)
    except gateway.WearableVoiceError as exc:
        raise _error(exc) from exc

    try:
        audio = await _limited_audio(request)
        gateway.validate_wav(audio)
        spoken = await run_in_threadpool(gateway.process_audio_turn, turn, audio)
        # Recheck revocation immediately before delivering any content.
        gateway.authenticate_device(token, turn.device_id)
        if not gateway.registry.complete(turn):
            raise gateway.WearableVoiceError("Wearable turn was stopped.", 409)
        return Response(
            content=spoken,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store", "X-VP3-Wearable-Protocol": gateway.VERSION},
        )
    except gateway.WearableVoiceError as exc:
        gateway.registry.discard(turn)
        raise _error(exc) from exc
    except Exception as exc:
        gateway.registry.discard(turn)
        # Never disclose transcript, audio, bearer token or provider errors.
        raise HTTPException(status_code=502, detail="Wearable voice turn could not complete.") from exc


@router.post("/stop/{turn_id}")
async def stop_turn(turn_id: str, request: Request):
    # Stop must remain available even if the feature switch was subsequently
    # disabled. An owner can also revoke the app, invalidating future calls.
    _guard_transport(request)
    paired, _ = _device(request)
    try:
        gateway.registry.stop(str(paired["app_key"]), turn_id)
    except gateway.WearableVoiceError as exc:
        raise _error(exc) from exc
    return Response(status_code=204, headers={"Cache-Control": "no-store"})

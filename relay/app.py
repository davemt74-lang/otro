from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from .database import (
    RelayAuthError,
    RelayClaimError,
    authenticate_session,
    claim_device,
    initialize_database,
    record_event,
    register_or_auth_device,
    rotate_session,
)


PROTOCOL = "homeserver-relay-v1"
SUBPROTOCOL = "homeserver.bridge.v1"
PUBLIC_OPERATIONS = {"capabilities", "pair.request", "pair.status"}
ALLOWED_OPERATIONS = {
    "capabilities",
    "pair.request",
    "pair.status",
    "chat",
    "conversations.list",
    "conversation.get",
    "contacts.search",
    "knowledge.search",
    "memory.read",
    "memory.write",
    "tools.list",
    "skills.list",
    "tool.execute",
    "action.status",
}


class ClaimRequest(BaseModel):
    claim_code: str = Field(min_length=8, max_length=40)


class RemoteRequest(BaseModel):
    operation: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    bearer_token: str | None = Field(default=None, max_length=512)


class ClaimLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - settings.claim_attempt_window_seconds
        with self._lock:
            bucket = self._attempts[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= settings.claim_attempt_limit:
                return False
            bucket.append(now)
            if not bucket:
                self._attempts.pop(key, None)
            return True


@dataclass
class DeviceConnection:
    device_id: str
    websocket: WebSocket
    connection_id: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    async def send_json(self, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > settings.max_message_bytes:
            raise RuntimeError("Relay message exceeds configured size limit.")
        async with self.send_lock:
            await self.websocket.send_text(encoded)

    async def request(self, operation: str, payload: dict, bearer_token: str | None) -> dict:
        request_id = f"relay-{secrets.token_urlsafe(16)}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending[request_id] = future
        try:
            await self.send_json(
                {
                    "type": "request",
                    "request_id": request_id,
                    "operation": operation,
                    "payload": payload,
                    "bearer_token": bearer_token,
                }
            )
            result = await asyncio.wait_for(
                future,
                timeout=settings.request_timeout_seconds,
            )
            if not isinstance(result, dict):
                raise RuntimeError("HomeServer returned an invalid relay response.")
            return result
        finally:
            self.pending.pop(request_id, None)

    def deliver(self, message: dict) -> bool:
        request_id = str(message.get("request_id") or "")
        future = self.pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(message)
        return True

    def fail_pending(self) -> None:
        for future in tuple(self.pending.values()):
            if not future.done():
                future.set_exception(RuntimeError("HomeServer disconnected from relay."))


class ConnectionManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[str, DeviceConnection] = {}

    async def attach(self, connection: DeviceConnection) -> None:
        previous: DeviceConnection | None = None
        async with self._lock:
            previous = self._connections.get(connection.device_id)
            self._connections[connection.device_id] = connection
        if previous is not None and previous is not connection:
            previous.fail_pending()
            try:
                await previous.websocket.close(code=1012)
            except Exception:
                pass

    async def detach(self, connection: DeviceConnection) -> None:
        async with self._lock:
            current = self._connections.get(connection.device_id)
            if current is connection:
                self._connections.pop(connection.device_id, None)
        connection.fail_pending()

    async def get(self, device_id: str) -> DeviceConnection | None:
        async with self._lock:
            return self._connections.get(device_id)

    async def notify_claimed(self, device_id: str) -> None:
        connection = await self.get(device_id)
        if connection is None:
            return
        await connection.send_json(
            {
                "type": "hello.ok",
                "claimed": True,
                "connection_id": connection.connection_id,
            }
        )


manager = ConnectionManager()
claim_limiter = ClaimLimiter()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(initialize_database)
    yield


app = FastAPI(
    title="HomeServer Remote Relay",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


def _bearer(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw.lower().startswith("bearer "):
        return ""
    return raw[7:].strip()


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Relay payload must be valid JSON.") from exc


async def _relay_session(request: Request) -> dict:
    token = _bearer(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status_code=401, detail="Relay session is required.")
    try:
        return await asyncio.to_thread(authenticate_session, token)
    except RelayAuthError as exc:
        raise HTTPException(status_code=401, detail="Relay session is invalid.") from exc


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "service": "homeserver-remote-relay",
        "protocol": PROTOCOL,
        "trust_model": "trusted-relay",
        "end_to_end_payload_encryption": False,
    }


@app.websocket("/bridge")
async def homeserver_bridge(websocket: WebSocket) -> None:
    requested = {
        item.strip()
        for item in str(websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if item.strip()
    }
    if SUBPROTOCOL not in requested:
        await websocket.close(code=4406)
        return

    device_id = str(websocket.headers.get("x-homeserver-device") or "").strip()
    device_secret = _bearer(websocket.headers.get("authorization"))
    if not device_id or not device_secret:
        await websocket.close(code=4401)
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    connection: DeviceConnection | None = None
    try:
        raw_hello = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        if len(raw_hello.encode("utf-8")) > settings.max_message_bytes:
            await websocket.close(code=1009)
            return
        try:
            hello = json.loads(raw_hello)
        except (TypeError, ValueError, json.JSONDecodeError):
            await websocket.close(code=4400)
            return
        if not isinstance(hello, dict):
            await websocket.close(code=4400)
            return
        if (
            hello.get("type") != "hello"
            or hello.get("protocol") != PROTOCOL
            or str(hello.get("device_id") or "") != device_id
        ):
            await websocket.close(code=4400)
            return

        try:
            device = await asyncio.to_thread(
                register_or_auth_device,
                device_id,
                device_secret,
            )
        except RelayAuthError:
            await websocket.close(code=4401)
            return

        connection = DeviceConnection(
            device_id=device_id,
            websocket=websocket,
            connection_id=f"conn-{secrets.token_urlsafe(12)}",
        )
        await manager.attach(connection)
        await connection.send_json(
            {
                "type": "hello.ok",
                "claimed": bool(device["claimed"]),
                "claim_code": device.get("claim_code"),
                "connection_id": connection.connection_id,
            }
        )
        await asyncio.to_thread(
            record_event,
            "device.connected",
            "completed",
            device_id=device_id,
            metadata={"version_length": len(str(hello.get("version") or ""))},
        )

        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > settings.max_message_bytes:
                await websocket.close(code=1009)
                return
            try:
                message = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                await websocket.close(code=4400)
                return
            if not isinstance(message, dict) or message.get("type") != "response":
                await asyncio.to_thread(
                    record_event,
                    "device.protocol",
                    "rejected",
                    device_id=device_id,
                    metadata={"message_type_length": len(str(message.get("type") if isinstance(message, dict) else ""))},
                )
                continue
            connection.deliver(message)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if connection is not None:
            await manager.detach(connection)
            await asyncio.to_thread(
                record_event,
                "device.disconnected",
                "completed",
                device_id=device_id,
            )


@app.post("/v1/claim")
async def claim(body: ClaimRequest, request: Request) -> dict:
    source = request.client.host if request.client else "unknown"
    if not claim_limiter.allow(source):
        raise HTTPException(status_code=429, detail="Too many claim attempts.")
    try:
        claimed = await asyncio.to_thread(claim_device, body.claim_code)
    except RelayClaimError as exc:
        raise HTTPException(status_code=404, detail="Claim code was not found or has expired.") from exc
    try:
        await manager.notify_claimed(claimed["device_id"])
    except Exception:
        pass
    await asyncio.to_thread(
        record_event,
        "client.claim",
        "completed",
        device_id=claimed["device_id"],
    )
    return {
        "device_id": claimed["device_id"],
        "relay_token": claimed["relay_token"],
        "trust_model": "trusted-relay",
        "end_to_end_payload_encryption": False,
    }


@app.get("/v1/session")
async def session_status(session: dict = Depends(_relay_session)) -> dict:
    connected = await manager.get(session["device_id"]) is not None
    return {
        "device_id": session["device_id"],
        "connected": connected,
        "trust_model": "trusted-relay",
    }


@app.post("/v1/session/rotate")
async def rotate(request: Request, session: dict = Depends(_relay_session)) -> dict:
    current = _bearer(request.headers.get("authorization"))
    try:
        rotated = await asyncio.to_thread(rotate_session, current)
    except RelayAuthError as exc:
        raise HTTPException(status_code=401, detail="Relay session is invalid.") from exc
    await asyncio.to_thread(
        record_event,
        "client.session",
        "rotated",
        device_id=session["device_id"],
    )
    return {
        "device_id": rotated["device_id"],
        "relay_token": rotated["relay_token"],
    }


@app.post("/v1/request")
async def relay_request(
    body: RemoteRequest,
    session: dict = Depends(_relay_session),
):
    operation = str(body.operation or "").strip()
    if operation not in ALLOWED_OPERATIONS:
        raise HTTPException(status_code=400, detail="Remote operation is not allowlisted by the relay.")
    if _json_size(body.payload) > settings.max_message_bytes:
        raise HTTPException(status_code=413, detail="Remote request payload is too large.")

    home_token = str(body.bearer_token or "").strip() or None
    if operation not in PUBLIC_OPERATIONS and (home_token is None or len(home_token) < 20):
        raise HTTPException(status_code=400, detail="A paired HomeServer bearer token is required.")

    connection = await manager.get(session["device_id"])
    if connection is None:
        raise HTTPException(status_code=503, detail="HomeServer is offline.")

    started = time.monotonic()
    request_id = f"http-{secrets.token_urlsafe(12)}"
    try:
        result = await connection.request(operation, body.payload, home_token)
    except asyncio.TimeoutError as exc:
        await asyncio.to_thread(
            record_event,
            "client.request",
            "failed",
            device_id=session["device_id"],
            operation=operation,
            request_id=request_id,
            metadata={"reason": "timeout"},
        )
        raise HTTPException(status_code=504, detail="HomeServer relay request timed out.") from exc
    except RuntimeError as exc:
        await asyncio.to_thread(
            record_event,
            "client.request",
            "failed",
            device_id=session["device_id"],
            operation=operation,
            request_id=request_id,
            metadata={"reason": "disconnected"},
        )
        raise HTTPException(status_code=503, detail="HomeServer disconnected from relay.") from exc

    status = result.get("status")
    ok = result.get("ok")
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if not isinstance(status, int) or status < 100 or status > 599 or not isinstance(ok, bool):
        raise HTTPException(status_code=502, detail="HomeServer returned an invalid relay response.")

    duration_ms = int((time.monotonic() - started) * 1000)
    await asyncio.to_thread(
        record_event,
        "client.request",
        "completed" if ok else "denied",
        device_id=session["device_id"],
        operation=operation,
        request_id=request_id,
        metadata={"http_status": status, "duration_ms": duration_ms},
    )
    return JSONResponse(
        status_code=status,
        content={"ok": ok, "status": status, "payload": payload},
    )

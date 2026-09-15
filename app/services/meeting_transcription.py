from __future__ import annotations

import asyncio
import hashlib
import importlib
import io
import math
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from . import local_voice, voice_settings

RUNTIME_VERSION = "v18.6"
CONTRACT = "vp3.meeting.transcription.v1"
OPERATION = "meeting.transcription.stream"
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIN_SEGMENT_MS = 700
SPEECH_RMS_THRESHOLD = 220
MAX_CALLBACK_ATTEMPTS = 3
CALLBACK_TIMEOUT_SECONDS = 12.0

_PUBLIC_ID = re.compile(r"^[a-f0-9]{32}$")
_IDEMPOTENCY = re.compile(r"^vp3-meeting-transcription:[a-f0-9]{32}$")
_IDENTITY = re.compile(r"^vp3-homeserver-[a-f0-9]{24}$")
_CALLBACK_TOKEN = re.compile(r"^v1850\.(\d{10})\.[a-f0-9]{64}$")
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{2,8}){0,3}$")

_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, "_MeetingJob"] = {}


class MeetingTranscriptionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class _MeetingJob:
    idempotency_key: str
    public_id: str
    room_name: str
    title: str
    app_key: str
    livekit_url: str
    livekit_identity: str
    livekit_token: str
    callback_url: str
    callback_token: str
    callback_expires_at: datetime
    language: str
    status: str = "starting"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    last_error: str | None = None
    callback_failures: int = 0
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)


def _rtc_module():
    try:
        return importlib.import_module("livekit.rtc")
    except Exception:
        return None


def status() -> dict[str, Any]:
    try:
        voice = local_voice.status()
        stt = voice.get("stt") if isinstance(voice, dict) else {}
        stt_ready = bool(isinstance(stt, dict) and stt.get("available"))
        model = str((voice.get("preferences") or {}).get("stt_model") or "")[:120]
    except Exception:
        stt_ready = False
        model = ""
    rtc_ready = _rtc_module() is not None
    return {
        "version": RUNTIME_VERSION,
        "operation": OPERATION,
        "contract": CONTRACT,
        "available": bool(stt_ready and rtc_ready),
        "local": True,
        "media_transport": "livekit",
        "livekit_sdk": rtc_ready,
        "stt_ready": stt_ready,
        "stt_provider": "whisper.cpp" if stt_ready else "",
        "stt_model": model,
        "final_only": True,
    }


def _safe_room(value: Any) -> str:
    room = str(value or "").strip()
    if not room or len(room) > 100 or any(ord(char) < 32 for char in room):
        raise MeetingTranscriptionError("meeting.room_name is invalid.", 422)
    return room


def _safe_livekit_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise MeetingTranscriptionError("livekit.url must be a valid ws:// or wss:// URL.", 422)
    if parsed.scheme == "ws" and not loopback:
        raise MeetingTranscriptionError("livekit.url requires wss:// except for loopback development.", 422)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MeetingTranscriptionError("livekit.url cannot contain credentials, query parameters, or a fragment.", 422)
    return raw


def _safe_callback_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MeetingTranscriptionError("callback.url must be a valid HTTP URL.", 422)
    if parsed.scheme != "https" and not loopback:
        raise MeetingTranscriptionError("callback.url requires HTTPS except for loopback development.", 422)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MeetingTranscriptionError("callback.url cannot contain credentials, query parameters, or a fragment.", 422)
    if not (parsed.path or "").endswith("/api/video-meeting-worker.php"):
        raise MeetingTranscriptionError("callback.url must target the canonical VP3 meeting worker.", 422)
    return raw


def _safe_expiry(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise MeetingTranscriptionError("callback.expires_at is required.", 422)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MeetingTranscriptionError("callback.expires_at must be an ISO-8601 timestamp.", 422) from exc
    if parsed.tzinfo is None:
        raise MeetingTranscriptionError("callback.expires_at must include a timezone.", 422)
    parsed = parsed.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = (parsed - now).total_seconds()
    if seconds < 5:
        raise MeetingTranscriptionError("Meeting callback capability is expired.", 409)
    if seconds > 43_260:
        raise MeetingTranscriptionError("Meeting callback capability lifetime is too long.", 422)
    return parsed


def _require_string(value: Any, label: str, *, minimum: int = 1, maximum: int = 8192) -> str:
    candidate = str(value or "").strip()
    if len(candidate) < minimum or len(candidate) > maximum:
        raise MeetingTranscriptionError(f"{label} is invalid.", 422)
    return candidate


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("contract") != CONTRACT:
        raise MeetingTranscriptionError("Unsupported meeting transcription contract.", 422)

    meeting = payload.get("meeting")
    livekit = payload.get("livekit")
    callback = payload.get("callback")
    transcription = payload.get("transcription")
    if not all(isinstance(item, dict) for item in (meeting, livekit, callback, transcription)):
        raise MeetingTranscriptionError("Meeting transcription payload is incomplete.", 422)

    public_id = str(meeting.get("public_id") or "").strip().lower()
    if not _PUBLIC_ID.fullmatch(public_id):
        raise MeetingTranscriptionError("meeting.public_id is invalid.", 422)

    idempotency_key = str(payload.get("idempotency_key") or "").strip().lower()
    if not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise MeetingTranscriptionError("idempotency_key is invalid.", 422)
    if idempotency_key != f"vp3-meeting-transcription:{public_id}":
        raise MeetingTranscriptionError("idempotency_key is not bound to this meeting.", 422)

    room_name = _safe_room(meeting.get("room_name"))
    livekit_identity = _require_string(livekit.get("participant_identity"), "livekit.participant_identity", maximum=128)
    if not _IDENTITY.fullmatch(livekit_identity):
        raise MeetingTranscriptionError("livekit.participant_identity is invalid.", 422)
    if livekit.get("subscribe_audio_only") is not True:
        raise MeetingTranscriptionError("HomeServer requires an audio-only LiveKit subscription.", 422)

    callback_token = _require_string(callback.get("bearer_token"), "callback.bearer_token", minimum=20, maximum=512)
    callback_match = _CALLBACK_TOKEN.fullmatch(callback_token)
    if callback_match is None:
        raise MeetingTranscriptionError("callback.bearer_token is not a scoped VP3 meeting capability.", 422)
    callback_expires_at = _safe_expiry(callback.get("expires_at"))
    if abs(int(callback_expires_at.timestamp()) - int(callback_match.group(1))) > 1:
        raise MeetingTranscriptionError("callback capability expiry does not match callback.expires_at.", 422)
    if callback.get("final_only") is not True or str(callback.get("source") or "").lower() != "homeserver":
        raise MeetingTranscriptionError("HomeServer callbacks must be final-only and source-bound.", 422)
    if transcription.get("final_only") is not True:
        raise MeetingTranscriptionError("HomeServer meeting transcription is final-only.", 422)

    language = str(transcription.get("language") or "en").strip()
    if not _LANGUAGE.fullmatch(language):
        raise MeetingTranscriptionError("transcription.language is invalid.", 422)
    try:
        preferences = voice_settings.get_preferences()
        model = voice_settings.STT_MODELS.get(str(preferences.get("stt_model") or "")) or {}
        model_language = str(model.get("language") or "").strip().lower()
    except Exception:
        model_language = ""
    requested_language = language.replace("_", "-").lower()
    if model_language and not (
        requested_language == model_language or requested_language.startswith(model_language + "-")
    ):
        raise MeetingTranscriptionError(
            f"Active local STT model does not support requested language: {language}.",
            409,
        )

    livekit_token = _require_string(
        livekit.get("participant_token"),
        "livekit.participant_token",
        minimum=40,
        maximum=16_384,
    )
    if _JWT.fullmatch(livekit_token) is None:
        raise MeetingTranscriptionError("livekit.participant_token must be a JWT.", 422)

    return {
        "idempotency_key": idempotency_key,
        "public_id": public_id,
        "room_name": room_name,
        "title": str(meeting.get("title") or "").strip()[:190],
        "livekit_url": _safe_livekit_url(livekit.get("url")),
        "livekit_identity": livekit_identity,
        "livekit_token": livekit_token,
        "callback_url": _safe_callback_url(callback.get("url")),
        "callback_token": callback_token,
        "callback_expires_at": callback_expires_at,
        "language": language,
    }


def _prune_jobs_locked(now: float) -> None:
    stale = [
        key
        for key, job in _JOBS.items()
        if job.status in {"completed", "failed", "stopped"} and now - job.updated_at > 3600
    ]
    for key in stale[:100]:
        _JOBS.pop(key, None)


def _snapshot(job: _MeetingJob, *, status_override: str | None = None) -> dict[str, Any]:
    state = status_override or job.status
    return {
        "ready": state in {"starting", "connecting", "running", "already_running"},
        "started": state in {"starting", "connecting", "running"},
        "status": state,
        "operation": OPERATION,
        "contract": CONTRACT,
        "meeting": job.public_id,
        "room_name": job.room_name,
        "idempotency_key": job.idempotency_key,
    }


def _set_status(job: _MeetingJob, state: str, error: str | None = None) -> None:
    with _JOBS_LOCK:
        job.status = state
        job.updated_at = time.time()
        if error is not None:
            job.last_error = error[:240]


def _record_callback_failure(job: _MeetingJob) -> None:
    with _JOBS_LOCK:
        job.callback_failures += 1
        job.updated_at = time.time()
        job.last_error = "callback_failed"


def start(payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    permissions = {str(value) for value in identity.get("permissions", []) if isinstance(value, str)}
    if "agent.chat" not in permissions:
        raise MeetingTranscriptionError("Permission required: agent.chat", 403)

    runtime = status()
    if not runtime["available"]:
        raise MeetingTranscriptionError("Local meeting transcription runtime is not ready.", 503)

    clean = _validate_payload(payload)
    now = time.time()
    with _JOBS_LOCK:
        _prune_jobs_locked(now)
        existing = _JOBS.get(clean["idempotency_key"])
        if existing is not None:
            if existing.public_id != clean["public_id"] or existing.room_name != clean["room_name"]:
                raise MeetingTranscriptionError("Idempotency key is already bound to another meeting.", 409)
            if existing.thread is not None and existing.thread.is_alive() and existing.status in {
                "starting", "connecting", "running"
            }:
                return _snapshot(existing, status_override="already_running")

        job = _MeetingJob(
            idempotency_key=clean["idempotency_key"],
            public_id=clean["public_id"],
            room_name=clean["room_name"],
            title=clean["title"],
            app_key=str(identity.get("app_key") or "")[:80],
            livekit_url=clean["livekit_url"],
            livekit_identity=clean["livekit_identity"],
            livekit_token=clean["livekit_token"],
            callback_url=clean["callback_url"],
            callback_token=clean["callback_token"],
            callback_expires_at=clean["callback_expires_at"],
            language=clean["language"],
        )
        thread = threading.Thread(
            target=_job_thread,
            args=(job,),
            name=f"homeserver-meeting-stt-{job.public_id[:10]}",
            daemon=True,
        )
        job.thread = thread
        _JOBS[job.idempotency_key] = job
        thread.start()
        return _snapshot(job)


def _job_thread(job: _MeetingJob) -> None:
    try:
        asyncio.run(_run_job(job))
    except Exception:
        _set_status(job, "failed", "runtime_failed")
    finally:
        # Credentials are intentionally memory-only and are destroyed once the
        # meeting worker exits. Status surfaces never expose them.
        job.livekit_token = ""
        job.callback_token = ""
        job.updated_at = time.time()


def _segment_preferences() -> tuple[int, int]:
    try:
        preferences = local_voice.status().get("preferences") or {}
        silence_ms = int(preferences.get("listen_silence_ms") or 900)
        max_segment_ms = int(preferences.get("max_segment_ms") or 30_000)
    except Exception:
        silence_ms, max_segment_ms = 900, 30_000
    return max(400, min(silence_ms, 3000)), max(5000, min(max_segment_ms, 60_000))


def _pcm_wav(pcm: bytes) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return target.getvalue()


def _frame_pcm_and_rms(frame: Any) -> tuple[bytes, int]:
    view = frame.data
    try:
        samples = view if view.format == "h" else view.cast("h")
        count = len(samples)
        if count < 1:
            return b"", 0
        square_sum = sum(int(sample) * int(sample) for sample in samples)
        rms = int(math.sqrt(square_sum / count))
        raw = samples.cast("B").tobytes()
        return raw, rms
    except (TypeError, ValueError):
        raw = bytes(view)
        if len(raw) % 2:
            raw = raw[:-1]
        if not raw:
            return b"", 0
        samples = memoryview(raw).cast("h")
        square_sum = sum(int(sample) * int(sample) for sample in samples)
        return raw, int(math.sqrt(square_sum / len(samples)))


def _source_key(job: _MeetingJob, participant_identity: str, track_sid: str, sequence: int) -> str:
    material = f"{job.public_id}|{participant_identity}|{track_sid}|{sequence}".encode("utf-8")
    return "hs-" + hashlib.sha256(material).hexdigest()[:40]


def _post_callback(job: _MeetingJob, body: dict[str, Any]) -> None:
    headers = {
        "Authorization": f"Bearer {job.callback_token}",
        "Content-Type": "application/json",
        "User-Agent": f"HomeServer-Meeting-STT/{RUNTIME_VERSION}",
    }
    last_error: Exception | None = None
    for attempt in range(MAX_CALLBACK_ATTEMPTS):
        if datetime.now(timezone.utc) >= job.callback_expires_at:
            raise MeetingTranscriptionError("Meeting callback capability expired.", 409)
        try:
            with httpx.Client(timeout=CALLBACK_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
                response = client.post(job.callback_url, json=body, headers=headers)
            if 200 <= response.status_code < 300:
                return
            if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                raise MeetingTranscriptionError("VP3 rejected the meeting transcript callback.", response.status_code)
            last_error = MeetingTranscriptionError("VP3 meeting transcript callback failed.", 502)
        except MeetingTranscriptionError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
        if attempt + 1 < MAX_CALLBACK_ATTEMPTS:
            time.sleep(0.35 * (2 ** attempt))
    raise MeetingTranscriptionError("VP3 meeting transcript callback failed after bounded retries.", 502) from last_error


async def _transcribe_segment(
    job: _MeetingJob,
    pcm: bytes,
    participant_identity: str,
    speaker_name: str,
    track_sid: str,
    sequence: int,
    start_ms: int,
    end_ms: int,
) -> None:
    if not pcm or end_ms <= start_ms:
        return
    try:
        result = await asyncio.to_thread(local_voice.transcribe, _pcm_wav(pcm))
    except Exception:
        return
    text = str(result.get("text") or "").strip()
    if not text:
        return
    body = {
        "meeting": job.public_id,
        "room_name": job.room_name,
        "participant_identity": participant_identity[:160],
        "speaker_name": speaker_name[:190],
        "start_ms": max(0, int(start_ms)),
        "end_ms": max(int(start_ms), int(end_ms)),
        "text": text[:20_000],
        "confidence": None,
        "source": "homeserver",
        "source_key": _source_key(job, participant_identity, track_sid, sequence),
        "is_final": True,
    }
    try:
        await asyncio.to_thread(_post_callback, job, body)
    except Exception:
        _record_callback_failure(job)


async def _consume_track(job: _MeetingJob, rtc: Any, track: Any, publication: Any, participant: Any) -> None:
    participant_identity = str(getattr(participant, "identity", "") or "unknown")[:160]
    speaker_name = str(getattr(participant, "name", "") or participant_identity)[:190]
    track_sid = str(getattr(publication, "sid", "") or getattr(track, "sid", "") or "audio")[:160]
    silence_ms, max_segment_ms = _segment_preferences()
    stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS, capacity=100)
    pcm = bytearray()
    speech_seen = False
    trailing_silence_ms = 0
    segment_start_ms = 0
    sequence = 0

    async def flush(end_ms: int) -> None:
        nonlocal pcm, speech_seen, trailing_silence_ms, segment_start_ms, sequence
        if speech_seen and pcm:
            duration_ms = int(len(pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) * 1000)
            if duration_ms >= MIN_SEGMENT_MS:
                sequence += 1
                await _transcribe_segment(
                    job,
                    bytes(pcm),
                    participant_identity,
                    speaker_name,
                    track_sid,
                    sequence,
                    segment_start_ms,
                    end_ms,
                )
        pcm = bytearray()
        speech_seen = False
        trailing_silence_ms = 0
        segment_start_ms = 0

    try:
        async for event in stream:
            if job.stop_event.is_set():
                break
            frame = event.frame
            raw, rms = _frame_pcm_and_rms(frame)
            if not raw:
                continue
            frame_ms = max(1, int(getattr(frame, "samples_per_channel", 0) * 1000 / max(1, getattr(frame, "sample_rate", SAMPLE_RATE))))
            now_ms = max(0, int((time.monotonic() - job.started_monotonic) * 1000))

            if not speech_seen:
                if rms < SPEECH_RMS_THRESHOLD:
                    continue
                speech_seen = True
                segment_start_ms = max(0, now_ms - frame_ms)

            pcm.extend(raw)
            trailing_silence_ms = trailing_silence_ms + frame_ms if rms < SPEECH_RMS_THRESHOLD else 0
            duration_ms = int(len(pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) * 1000)
            if duration_ms >= max_segment_ms or trailing_silence_ms >= silence_ms:
                await flush(now_ms)
    finally:
        end_ms = max(0, int((time.monotonic() - job.started_monotonic) * 1000))
        await flush(end_ms)
        try:
            await stream.aclose()
        except Exception:
            pass


async def _run_job(job: _MeetingJob) -> None:
    rtc = _rtc_module()
    if rtc is None:
        _set_status(job, "failed", "livekit_sdk_unavailable")
        return

    _set_status(job, "connecting")
    room = rtc.Room()
    disconnected = asyncio.Event()
    stream_tasks: set[asyncio.Task] = set()
    active_tracks: set[str] = set()

    def schedule_track(track: Any, publication: Any, participant: Any) -> None:
        if getattr(track, "kind", None) != rtc.TrackKind.KIND_AUDIO:
            return
        sid = str(getattr(publication, "sid", "") or getattr(track, "sid", "") or id(track))
        if sid in active_tracks or len(active_tracks) >= 32:
            return
        active_tracks.add(sid)
        task = asyncio.create_task(_consume_track(job, rtc, track, publication, participant))
        stream_tasks.add(task)
        task.add_done_callback(stream_tasks.discard)

    @room.on("track_published")
    def on_track_published(publication: Any, participant: Any) -> None:
        if getattr(publication, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            try:
                publication.set_subscribed(True)
            except Exception:
                pass

    @room.on("track_subscribed")
    def on_track_subscribed(track: Any, publication: Any, participant: Any) -> None:
        schedule_track(track, publication, participant)

    @room.on("disconnected")
    def on_disconnected(*_: Any) -> None:
        disconnected.set()

    try:
        options = rtc.RoomOptions(auto_subscribe=False, connect_timeout=15.0)
        # Do not externally cancel Room.connect: current LiveKit Python FFI
        # requires its connection handshake to finish cleanly.
        await room.connect(job.livekit_url, job.livekit_token, options=options)
        _set_status(job, "running")

        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                if getattr(publication, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                    continue
                try:
                    publication.set_subscribed(True)
                except Exception:
                    continue
                track = getattr(publication, "track", None)
                if track is not None:
                    schedule_track(track, publication, participant)

        while not disconnected.is_set() and not job.stop_event.is_set():
            if datetime.now(timezone.utc) >= job.callback_expires_at:
                job.stop_event.set()
                break
            await asyncio.sleep(0.25)

        if job.stop_event.is_set() and not disconnected.is_set():
            try:
                await room.disconnect()
            except Exception:
                pass

        if stream_tasks:
            _, pending = await asyncio.wait(tuple(stream_tasks), timeout=120.0)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if job.status not in {"failed", "stopped"}:
            _set_status(job, "completed")
    except Exception:
        _set_status(job, "failed", "livekit_runtime_failed")
    finally:
        try:
            await room.disconnect()
        except Exception:
            pass


def stop_all() -> None:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    for job in jobs:
        if job.thread is not None and job.thread.is_alive():
            job.stop_event.set()
            _set_status(job, "stopped")

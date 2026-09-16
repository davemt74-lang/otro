from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _publisher_token(api_key: str, api_secret: str, room_name: str) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": api_key,
                "sub": "vp3-phase-18-8-live-publisher",
                "name": "VP3 Phase 18.8 Synthetic Publisher",
                "nbf": now - 5,
                "exp": now + 1800,
                "video": {
                    "room": room_name,
                    "roomJoin": True,
                    "canPublish": True,
                    "canSubscribe": False,
                    "canPublishData": False,
                },
            },
            separators=(",", ":"),
        ).encode()
    )
    material = f"{header}.{payload}".encode("ascii")
    signature = _b64url(hmac.new(api_secret.encode(), material, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _load_wav(path: Path) -> tuple[int, bytes]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1:
            raise RuntimeError("Phase 18.8 live validation WAV must be mono.")
        if wav.getsampwidth() != 2:
            raise RuntimeError("Phase 18.8 live validation WAV must use 16-bit PCM.")
        sample_rate = int(wav.getframerate())
        if sample_rate < 8000 or sample_rate > 48000:
            raise RuntimeError("Phase 18.8 live validation WAV sample rate is unsupported.")
        return sample_rate, wav.readframes(wav.getnframes())


async def _publish_wav(url: str, token: str, sample_rate: int, pcm: bytes, ready: asyncio.Event) -> None:
    from livekit import rtc

    room = rtc.Room()
    source = rtc.AudioSource(sample_rate=sample_rate, num_channels=1, queue_size_ms=100)
    track = rtc.LocalAudioTrack.create_audio_track("phase-18-8-synthetic-speech", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    try:
        await room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=False))
        await room.local_participant.publish_track(track, options)
        await ready.wait()

        samples_per_frame = max(1, sample_rate // 100)  # 10 ms
        bytes_per_frame = samples_per_frame * 2
        for offset in range(0, len(pcm), bytes_per_frame):
            chunk = pcm[offset : offset + bytes_per_frame]
            frame = rtc.AudioFrame.create(sample_rate, 1, samples_per_frame)
            target = frame.data.cast("B")
            target[:] = b"\x00" * len(target)
            target[: len(chunk)] = chunk
            await source.capture_frame(frame)

        # Give the production speech segmenter enough silence to finalize.
        silence = b"\x00" * bytes_per_frame
        for _ in range(140):
            frame = rtc.AudioFrame.create(sample_rate, 1, samples_per_frame)
            frame.data.cast("B")[:] = silence
            await source.capture_frame(frame)
    finally:
        try:
            await room.disconnect()
        except Exception:
            pass


async def main() -> None:
    from app.services import meeting_transcription, meeting_transcription_control

    payload_raw = os.getenv("VP3_MEETING_TRANSCRIPTION_PAYLOAD_JSON", "").strip()
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    wav_path = Path(os.getenv("VP3_MEETING_TEST_WAV", "").strip())
    timeout_seconds = max(20, min(int(os.getenv("VP3_MEETING_LIVE_TIMEOUT", "120")), 300))

    missing = []
    if not payload_raw:
        missing.append("VP3_MEETING_TRANSCRIPTION_PAYLOAD_JSON")
    if not api_key:
        missing.append("LIVEKIT_API_KEY")
    if not api_secret:
        missing.append("LIVEKIT_API_SECRET")
    if not str(wav_path):
        missing.append("VP3_MEETING_TEST_WAV")
    if missing:
        raise RuntimeError("Missing Phase 18.8 live validation configuration: " + ", ".join(missing))
    if not wav_path.is_file():
        raise RuntimeError("VP3_MEETING_TEST_WAV does not exist.")

    payload = json.loads(payload_raw)
    clean = meeting_transcription._validate_payload(payload)
    runtime = meeting_transcription.status()
    if not runtime.get("available") or not runtime.get("stt_ready") or not runtime.get("livekit_sdk"):
        raise RuntimeError("HomeServer local STT + LiveKit runtime is not production-ready.")

    sample_rate, pcm = _load_wav(wav_path)
    publisher = _publisher_token(api_key, api_secret, clean["room_name"])
    ready_to_publish = asyncio.Event()
    callback_successes: list[str] = []
    original_callback = meeting_transcription._post_callback

    def audited_callback(job, body):
        original_callback(job, body)
        callback_successes.append(str(body.get("source_key") or ""))

    meeting_transcription._post_callback = audited_callback
    identity = {"app_key": "vp3-phase-18-8-live", "permissions": ["agent.chat"]}
    publish_task = asyncio.create_task(
        _publish_wav(clean["livekit_url"], publisher, sample_rate, pcm, ready_to_publish)
    )

    try:
        started = await asyncio.to_thread(meeting_transcription.start, payload, identity)
        if started.get("status") not in {"running", "already_running"}:
            raise RuntimeError(f"HomeServer did not reach running state: {started.get('status')}")
        ready_to_publish.set()
        await publish_task

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while not callback_successes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
        if not callback_successes:
            raise RuntimeError("No final HomeServer transcript callback was accepted by VP3.")

        status = meeting_transcription_control.status_for({"meeting": clean["public_id"]}, identity)
        if status.get("callback_failures"):
            raise RuntimeError("HomeServer recorded a VP3 callback failure during live validation.")
        if status.get("transcription_failures"):
            raise RuntimeError("HomeServer recorded a local STT failure during live validation.")

        meeting_transcription_control.stop_for({"meeting": clean["public_id"]}, identity)
        print(
            json.dumps(
                {
                    "ok": True,
                    "phase": "18.8",
                    "meeting": clean["public_id"],
                    "media_transport": "livekit",
                    "stt": "local-whisper.cpp",
                    "accepted_final_callbacks": len(callback_successes),
                },
                separators=(",", ":"),
            )
        )
    finally:
        ready_to_publish.set()
        if not publish_task.done():
            publish_task.cancel()
            try:
                await publish_task
            except asyncio.CancelledError:
                pass
        meeting_transcription._post_callback = original_callback
        try:
            meeting_transcription_control.stop_for({"meeting": clean["public_id"]}, identity)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())

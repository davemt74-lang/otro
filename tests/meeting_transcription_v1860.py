from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from array import array
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def payload() -> dict:
    public_id = "a" * 32
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    expires_ts = int(expires.timestamp())
    return {
        "contract": "vp3.meeting.transcription.v1",
        "idempotency_key": f"vp3-meeting-transcription:{public_id}",
        "meeting": {
            "public_id": public_id,
            "room_name": "vp3-meeting-aabbcc",
            "title": "Private planning meeting",
        },
        "livekit": {
            "url": "wss://example.livekit.cloud",
            "participant_identity": "vp3-homeserver-" + ("b" * 24),
            "participant_token": ("h" * 24) + "." + ("p" * 24) + "." + ("s" * 24),
            "subscribe_audio_only": True,
        },
        "callback": {
            "url": "https://vp3.example/api/video-meeting-worker.php",
            "bearer_token": f"v1850.{expires_ts}." + ("c" * 64),
            "expires_at": expires.isoformat(),
            "final_only": True,
            "source": "homeserver",
        },
        "transcription": {"language": "en", "final_only": True},
    }


with tempfile.TemporaryDirectory(prefix="homeserver-meeting-v1860-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import meeting_transcription, remote_bridge  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    original_status = meeting_transcription.status
    original_start = meeting_transcription.start
    original_job_thread = meeting_transcription._job_thread
    original_transcribe = meeting_transcription.local_voice.transcribe
    original_post_callback = meeting_transcription._post_callback

    runtime_ready = {
        "version": "v18.6",
        "operation": "meeting.transcription.stream",
        "contract": "vp3.meeting.transcription.v1",
        "available": True,
        "local": True,
        "media_transport": "livekit",
        "livekit_sdk": True,
        "stt_ready": True,
        "stt_provider": "whisper.cpp",
        "stt_model": "tiny.en-q8_0",
        "final_only": True,
    }

    try:
        with TestClient(app) as client:
            scheduler.stop()
            owner = client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            )
            assert owner.status_code == 200, owner.text

            # Capability is dynamic: an authenticated app must hold the existing
            # agent compute authority and the local runtime must actually be ready.
            meeting_transcription.status = lambda: dict(runtime_ready)
            request = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "vp3-meeting-test",
                    "app_name": "VP3 Meeting Test",
                    "permissions": ["agent.chat"],
                },
            ).json()
            approved = client.post("/api/v1/pairing/approve", json={"code": request["code"]})
            assert approved.status_code == 200, approved.text
            headers = {"Authorization": f"Bearer {request['claim_token']}"}
            registry = client.get("/api/v1/capability-registry", headers=headers)
            assert registry.status_code == 200, registry.text
            registry_body = registry.json()
            assert "meeting.transcription.stream" in registry_body["operations"]
            assert registry_body["meeting_transcription"]["available"] is True
            encoded_registry = registry.text.lower()
            assert "participant_token" not in encoded_registry
            assert "bearer_token" not in encoded_registry

            denied_request = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "vp3-meeting-denied",
                    "app_name": "VP3 Meeting Denied",
                    "permissions": ["knowledge.search"],
                },
            ).json()
            assert client.post(
                "/api/v1/pairing/approve",
                json={"code": denied_request["code"]},
            ).status_code == 200
            denied_headers = {"Authorization": f"Bearer {denied_request['claim_token']}"}
            denied_registry = client.get("/api/v1/capability-registry", headers=denied_headers)
            assert "meeting.transcription.stream" not in denied_registry.json()["operations"]

            meeting_transcription.status = lambda: {**runtime_ready, "available": False, "stt_ready": False}
            unavailable_registry = client.get("/api/v1/capability-registry", headers=headers)
            assert "meeting.transcription.stream" not in unavailable_registry.json()["operations"]

            # Exact VP3 v18.5 contract validation.
            valid = payload()
            clean = meeting_transcription._validate_payload(valid)
            assert clean["public_id"] == "a" * 32
            assert clean["room_name"] == "vp3-meeting-aabbcc"
            assert clean["livekit_identity"].startswith("vp3-homeserver-")

            invalid = payload()
            invalid["contract"] = "vp3.meeting.transcription.v0"
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("unsupported contract was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            invalid = payload()
            invalid["livekit"]["subscribe_audio_only"] = False
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("non-audio-only subscription was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            invalid = payload()
            invalid["callback"]["url"] = "http://vp3.example/api/video-meeting-worker.php"
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("insecure remote callback was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            invalid = payload()
            invalid["callback"]["url"] = "https://vp3.example/not-the-worker"
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("non-canonical callback path was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            invalid = payload()
            invalid["callback"]["bearer_token"] = "global-worker-secret-would-not-match"
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("unscoped callback credential was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            invalid = payload()
            invalid["livekit"]["participant_token"] = "not-a-jwt-" * 8
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("non-JWT LiveKit token was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            invalid = payload()
            invalid["transcription"]["language"] = "es"
            try:
                meeting_transcription._validate_payload(invalid)
                raise AssertionError("unsupported active Whisper language was accepted")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 409

            # Local PCM helpers must emit valid 16-bit mono WAV for whisper.cpp.
            class Frame:
                sample_rate = 16000
                samples_per_channel = 8
                data = memoryview(array("h", [1000, -1000, 800, -800, 600, -600, 400, -400]))

            pcm, rms = meeting_transcription._frame_pcm_and_rms(Frame())
            assert len(pcm) == 16
            assert rms > 0
            wav = meeting_transcription._pcm_wav(pcm * 2000)
            assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
            meeting_transcription.local_voice._validate_wav(wav)

            # Final callbacks keep the canonical worker shape and never contain
            # HomeServer/VP3 long-lived credentials.
            captured = []
            meeting_transcription.local_voice.transcribe = lambda _: {
                "text": "Local transcript segment",
                "provider": "whisper.cpp",
                "local": True,
            }
            meeting_transcription._post_callback = lambda job, body: captured.append(body)
            valid = payload()
            clean = meeting_transcription._validate_payload(valid)
            job = meeting_transcription._MeetingJob(
                idempotency_key=clean["idempotency_key"],
                public_id=clean["public_id"],
                room_name=clean["room_name"],
                title=clean["title"],
                app_key="vp3-meeting-test",
                livekit_url=clean["livekit_url"],
                livekit_identity=clean["livekit_identity"],
                livekit_token=clean["livekit_token"],
                callback_url=clean["callback_url"],
                callback_token=clean["callback_token"],
                callback_expires_at=clean["callback_expires_at"],
                language=clean["language"],
            )
            asyncio.run(
                meeting_transcription._transcribe_segment(
                    job,
                    pcm * 2000,
                    "user-alice",
                    "Alice",
                    "track-1",
                    1,
                    100,
                    2100,
                )
            )
            assert len(captured) == 1
            segment = captured[0]
            assert segment["meeting"] == "a" * 32
            assert segment["room_name"] == "vp3-meeting-aabbcc"
            assert segment["source"] == "homeserver"
            assert segment["is_final"] is True
            assert segment["text"] == "Local transcript segment"
            assert segment["source_key"].startswith("hs-") and len(segment["source_key"]) == 43
            serialized = json.dumps(segment)
            assert clean["livekit_token"] not in serialized
            assert clean["callback_token"] not in serialized

            # Idempotency is active while a worker owns the meeting.
            meeting_transcription.local_voice.transcribe = original_transcribe
            meeting_transcription._post_callback = original_post_callback
            meeting_transcription.status = lambda: dict(runtime_ready)
            release = threading.Event()

            def hold_job(job):
                release.wait(2)

            meeting_transcription._job_thread = hold_job
            meeting_transcription._JOBS.clear()
            first = meeting_transcription.start(payload(), {
                "app_key": "vp3-meeting-test",
                "permissions": ["agent.chat"],
            })
            second = meeting_transcription.start(payload(), {
                "app_key": "vp3-meeting-test",
                "permissions": ["agent.chat"],
            })
            assert first["started"] is True and first["status"] == "starting"
            assert second["started"] is False and second["status"] == "already_running"
            active = meeting_transcription._JOBS[first["idempotency_key"]]
            snapshot = json.dumps(meeting_transcription._snapshot(active))
            assert active.livekit_token not in snapshot
            assert active.callback_token not in snapshot
            release.set()
            active.thread.join(timeout=3)
            assert not active.thread.is_alive()

            # Relay dispatch uses the paired bearer identity and returns the
            # response envelope expected by the existing HomeServer bridge.
            meeting_transcription._job_thread = original_job_thread
            captured_identity = {}

            def fake_start(body, identity):
                captured_identity.update(identity)
                assert body["contract"] == "vp3.meeting.transcription.v1"
                return {
                    "ready": True,
                    "started": True,
                    "status": "started",
                    "operation": "meeting.transcription.stream",
                }

            meeting_transcription.start = fake_start
            relayed = remote_bridge.dispatch_remote_request(
                "meeting.transcription.stream",
                payload(),
                request["claim_token"],
            )
            assert relayed["status"] == 200 and relayed["ok"] is True
            assert relayed["payload"]["ready"] is True
            assert captured_identity["app_key"] == "vp3-meeting-test"

            try:
                remote_bridge.dispatch_remote_request(
                    "meeting.transcription.stream",
                    payload(),
                    None,
                )
                raise AssertionError("meeting transcription accepted a missing pairing token")
            except remote_bridge.RemoteBridgeError:
                pass

            try:
                remote_bridge.dispatch_remote_request(
                    "meeting.transcription.stream",
                    payload(),
                    denied_request["claim_token"],
                )
                raise AssertionError("meeting transcription accepted an app without agent.chat")
            except remote_bridge.RemoteBridgeError:
                pass

    finally:
        meeting_transcription.status = original_status
        meeting_transcription.start = original_start
        meeting_transcription._job_thread = original_job_thread
        meeting_transcription.local_voice.transcribe = original_transcribe
        meeting_transcription._post_callback = original_post_callback
        meeting_transcription._JOBS.clear()

print("HomeServer Phase 18.6 local meeting STT contract passed")

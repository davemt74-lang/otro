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
            "url": "https://vp3.me/api/video-meeting-worker.php",
            "bearer_token": f"v1850.{expires_ts}." + ("c" * 64),
            "expires_at": expires.isoformat(),
            "final_only": True,
            "source": "homeserver",
        },
        "transcription": {"language": "en", "final_only": True},
    }


def assert_rejected(body: dict, status_code: int) -> None:
    from app.services import meeting_transcription

    try:
        meeting_transcription._validate_payload(body)
        raise AssertionError("invalid meeting transcription payload was accepted")
    except meeting_transcription.MeetingTranscriptionError as exc:
        assert exc.status_code == status_code, str(exc)


with tempfile.TemporaryDirectory(prefix="homeserver-meeting-v1860-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import meeting_transcription, meeting_transcription_remote, remote_bridge  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    original_status = meeting_transcription.status
    original_start = meeting_transcription.start
    original_job_thread = meeting_transcription._job_thread
    original_transcribe = meeting_transcription.local_voice.transcribe
    original_post_callback = meeting_transcription._post_callback
    original_claimed_cloud_ready = meeting_transcription_remote._claimed_cloud_ready

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
        meeting_transcription_remote._claimed_cloud_ready = lambda: True
        with TestClient(app) as client:
            scheduler.stop()
            owner = client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            )
            assert owner.status_code == 200, owner.text

            # Capability discovery is paired-app scoped, requires the existing
            # agent compute authority, and is only advertised while local STT is ready.
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

            meeting_transcription.status = lambda: dict(runtime_ready)
            meeting_transcription_remote._claimed_cloud_ready = lambda: False
            unclaimed_registry = client.get("/api/v1/capability-registry", headers=headers)
            assert "meeting.transcription.stream" not in unclaimed_registry.json()["operations"]
            meeting_transcription_remote._claimed_cloud_ready = lambda: True

            # Exact VP3 v18.5 contract validation and callback destination binding.
            valid = payload()
            clean = meeting_transcription._validate_payload(valid)
            assert clean["public_id"] == "a" * 32
            assert clean["room_name"] == "vp3-meeting-aabbcc"
            assert clean["livekit_identity"].startswith("vp3-homeserver-")

            invalid = payload()
            invalid["contract"] = "vp3.meeting.transcription.v0"
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["livekit"]["subscribe_audio_only"] = False
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["callback"]["url"] = "http://vp3.me/api/video-meeting-worker.php"
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["callback"]["url"] = "https://vp3.me/not-the-worker"
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["callback"]["url"] = "https://attacker.example/api/video-meeting-worker.php"
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["callback"]["bearer_token"] = "global-worker-secret-would-not-match"
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["livekit"]["participant_token"] = "not-a-jwt-" * 8
            assert_rejected(invalid, 422)

            invalid = payload()
            invalid["transcription"]["language"] = "es"
            assert_rejected(invalid, 409)

            # Local PCM helpers emit valid 16-bit mono WAV for the existing Whisper runtime.
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
            clean = meeting_transcription._validate_payload(payload())
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

            # An exhausted callback failure is fail-closed, and repeated local STT
            # failures stop the worker instead of silently discarding a meeting.
            failure_job = meeting_transcription._MeetingJob(**{
                key: value for key, value in {
                    "idempotency_key": clean["idempotency_key"],
                    "public_id": clean["public_id"],
                    "room_name": clean["room_name"],
                    "title": clean["title"],
                    "app_key": "vp3-meeting-test",
                    "livekit_url": clean["livekit_url"],
                    "livekit_identity": clean["livekit_identity"],
                    "livekit_token": clean["livekit_token"],
                    "callback_url": clean["callback_url"],
                    "callback_token": clean["callback_token"],
                    "callback_expires_at": clean["callback_expires_at"],
                    "language": clean["language"],
                }.items()
            })
            failure_job.status = "running"
            meeting_transcription._record_callback_failure(failure_job)
            assert failure_job.status == "failed" and failure_job.stop_event.is_set()
            failure_job.stop_event.clear()
            failure_job.status = "running"
            failure_job.transcription_failures = 0
            for _ in range(3):
                meeting_transcription._record_transcription_failure(failure_job)
            assert failure_job.status == "failed" and failure_job.stop_event.is_set()

            # Idempotency is active only after the worker truthfully reports that
            # the LiveKit subscriber has reached the running state.
            meeting_transcription.local_voice.transcribe = original_transcribe
            meeting_transcription._post_callback = original_post_callback
            release = threading.Event()

            def hold_job(active_job):
                meeting_transcription._set_status(active_job, "running")
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
            assert first["started"] is True and first["status"] == "running"
            assert second["started"] is False and second["status"] == "already_running"
            active = meeting_transcription._JOBS[first["idempotency_key"]]
            snapshot = json.dumps(meeting_transcription._snapshot(active))
            assert active.livekit_token not in snapshot
            assert active.callback_token not in snapshot
            release.set()
            active.thread.join(timeout=3)
            assert not active.thread.is_alive()

            # A worker that never reaches running must fail closed rather than
            # returning a false ready/active acknowledgement to VP3 Cloud.
            original_startup_wait = meeting_transcription.STARTUP_WAIT_SECONDS
            meeting_transcription.STARTUP_WAIT_SECONDS = 0.05
            meeting_transcription._JOBS.clear()

            def never_ready(active_job):
                active_job.stop_event.wait(0.5)

            meeting_transcription._job_thread = never_ready
            try:
                meeting_transcription.start(payload(), {
                    "app_key": "vp3-meeting-test",
                    "permissions": ["agent.chat"],
                })
                raise AssertionError("meeting transcription acknowledged a worker that never became ready")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 503
            finally:
                meeting_transcription.STARTUP_WAIT_SECONDS = original_startup_wait
            failed_start = next(iter(meeting_transcription._JOBS.values()))
            assert failed_start.status == "failed"
            assert failed_start.stop_event.is_set()
            failed_start.thread.join(timeout=1)
            assert not failed_start.thread.is_alive()

            # Relay dispatch requires both the paired app credential and a live,
            # claimed VP3 Cloud bridge.
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
                remote_bridge.dispatch_remote_request("meeting.transcription.stream", payload(), None)
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

            meeting_transcription_remote._claimed_cloud_ready = lambda: False
            try:
                remote_bridge.dispatch_remote_request(
                    "meeting.transcription.stream",
                    payload(),
                    request["claim_token"],
                )
                raise AssertionError("meeting transcription accepted an unclaimed relay")
            except remote_bridge.RemoteBridgeError:
                pass
            meeting_transcription_remote._claimed_cloud_ready = lambda: True

            # HomeServer shutdown marks and stops active meeting workers.
            meeting_transcription.start = original_start
            shutdown_clean = meeting_transcription._validate_payload(payload())
            shutdown_job = meeting_transcription._MeetingJob(
                idempotency_key="vp3-meeting-transcription:" + ("d" * 32),
                public_id="d" * 32,
                room_name="vp3-meeting-shutdown",
                title="Shutdown test",
                app_key="vp3-meeting-test",
                livekit_url=shutdown_clean["livekit_url"],
                livekit_identity=shutdown_clean["livekit_identity"],
                livekit_token=shutdown_clean["livekit_token"],
                callback_url=shutdown_clean["callback_url"],
                callback_token=shutdown_clean["callback_token"],
                callback_expires_at=shutdown_clean["callback_expires_at"],
                language=shutdown_clean["language"],
                status="running",
            )

            def until_stopped():
                shutdown_job.stop_event.wait(2)

            shutdown_job.thread = threading.Thread(target=until_stopped, daemon=True)
            shutdown_job.thread.start()
            meeting_transcription._JOBS[shutdown_job.idempotency_key] = shutdown_job
            meeting_transcription.stop_all(join_timeout=1.0)
            assert shutdown_job.status == "stopped"
            assert shutdown_job.stop_event.is_set()
            assert not shutdown_job.thread.is_alive()

    finally:
        meeting_transcription.status = original_status
        meeting_transcription.start = original_start
        meeting_transcription._job_thread = original_job_thread
        meeting_transcription.local_voice.transcribe = original_transcribe
        meeting_transcription._post_callback = original_post_callback
        meeting_transcription_remote._claimed_cloud_ready = original_claimed_cloud_ready
        meeting_transcription._JOBS.clear()

print("HomeServer Phase 18.6 local meeting STT contract passed")

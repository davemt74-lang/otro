from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-meeting-v1880-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.services import (  # noqa: E402
        capability_registry,
        meeting_transcription,
        meeting_transcription_control,
        meeting_transcription_remote,
        remote_bridge,
    )

    original_identity = meeting_transcription_remote._identity
    original_claimed = meeting_transcription_remote._claimed_cloud_ready
    original_status = meeting_transcription.status

    public_id = "c" * 32
    key = f"vp3-meeting-transcription:{public_id}"
    identity = {"app_key": "vp3-phase-18-8", "permissions": ["agent.chat"]}
    other_identity = {"app_key": "other-wrapper", "permissions": ["agent.chat"]}

    job = meeting_transcription._MeetingJob(
        idempotency_key=key,
        public_id=public_id,
        room_name="vp3-meeting-production-v1880",
        title="Phase 18.8 production readiness",
        app_key=identity["app_key"],
        livekit_url="wss://example.livekit.cloud",
        livekit_identity="vp3-homeserver-" + ("d" * 24),
        livekit_token=("h" * 24) + "." + ("p" * 24) + "." + ("s" * 24),
        callback_url="https://vp3.me/api/video-meeting-worker.php",
        callback_token="v1850.2000000000." + ("e" * 64),
        callback_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        language="en",
    )
    job.status = "running"
    job.thread = threading.current_thread()

    try:
        meeting_transcription._JOBS.clear()
        meeting_transcription._JOBS[key] = job
        meeting_transcription.status = lambda: {
            "version": "v18.6",
            "operation": meeting_transcription.OPERATION,
            "contract": meeting_transcription.CONTRACT,
            "available": True,
            "local": True,
            "media_transport": "livekit",
            "livekit_sdk": True,
            "stt_ready": True,
            "stt_provider": "whisper.cpp",
            "stt_model": "tiny.en-q8_0",
            "final_only": True,
        }

        status = meeting_transcription_control.status_for({"meeting": public_id}, identity)
        assert status["status"] == "running"
        assert status["ready"] is True and status["active"] is True
        assert status["meeting"] == public_id
        encoded = json.dumps(status)
        assert job.livekit_token not in encoded
        assert job.callback_token not in encoded

        by_key = meeting_transcription_control.status_for({"idempotency_key": key}, identity)
        assert by_key["meeting"] == public_id

        missing = meeting_transcription_control.status_for({"meeting": "a" * 32}, identity)
        assert missing["status"] == "not_found" and missing["active"] is False

        try:
            meeting_transcription_control.status_for({"meeting": public_id}, other_identity)
            raise AssertionError("another paired wrapper could inspect a meeting transcription job")
        except meeting_transcription.MeetingTranscriptionError as exc:
            assert exc.status_code == 404

        try:
            meeting_transcription_control.status_for(
                {"meeting": public_id, "idempotency_key": "vp3-meeting-transcription:" + ("f" * 32)},
                identity,
            )
            raise AssertionError("mismatched meeting/idempotency binding was accepted")
        except meeting_transcription.MeetingTranscriptionError as exc:
            assert exc.status_code == 422

        runtime = meeting_transcription_control.runtime_status()
        assert runtime["production_control_version"] == "v18.8"
        assert runtime["active_jobs"] == 1
        assert runtime["state_counts"]["running"] == 1
        assert runtime["status_operation"] == "meeting.transcription.status"
        assert runtime["stop_operation"] == "meeting.transcription.stop"

        registry_api = (ROOT_DIR / "app" / "capability_registry_api.py").read_text(encoding="utf-8")
        assert 'registry["meeting_transcription"] = meeting_transcription.status()' in registry_api
        assert "meeting_transcription_runtime_status" not in registry_api

        meeting_transcription_remote.install()
        meeting_transcription_remote._identity = lambda token: identity
        meeting_transcription_remote._claimed_cloud_ready = lambda: True
        meeting_transcription_control.install()

        operations = capability_registry._operations({"agent.chat"}, False)
        assert meeting_transcription.OPERATION in operations
        assert meeting_transcription_control.STATUS_OPERATION in operations
        assert meeting_transcription_control.STOP_OPERATION in operations

        relayed_status = remote_bridge.dispatch_remote_request(
            meeting_transcription_control.STATUS_OPERATION,
            {"meeting": public_id},
            "synthetic-paired-app-token-for-v1880",
        )
        assert relayed_status["status"] == 200 and relayed_status["ok"] is True
        assert relayed_status["payload"]["status"] == "running"

        relayed_stop = remote_bridge.dispatch_remote_request(
            meeting_transcription_control.STOP_OPERATION,
            {"meeting": public_id},
            "synthetic-paired-app-token-for-v1880",
        )
        assert relayed_stop["status"] == 200 and relayed_stop["ok"] is True
        assert relayed_stop["payload"]["stop_requested"] is True
        assert relayed_stop["payload"]["stopped"] is False
        assert relayed_stop["payload"]["status"] == "stopping"
        assert job.stop_event.is_set()
        assert job.status == "stopping"

        terminal = meeting_transcription_control.runtime_status()
        assert terminal["active_jobs"] == 1
        assert terminal["state_counts"]["stopping"] == 1

        # Terminal jobs answer truthfully: no new stop is requested, and only a
        # genuinely stopped worker is reported as stopped.
        job.status = "completed"
        completed_stop = meeting_transcription_control.stop_for({"meeting": public_id}, identity)
        assert completed_stop["stop_requested"] is False
        assert completed_stop["stopped"] is False
        assert completed_stop["status"] == "completed"
        job.status = "stopped"
        stopped_again = meeting_transcription_control.stop_for({"meeting": public_id}, identity)
        assert stopped_again["stop_requested"] is False
        assert stopped_again["stopped"] is True

        invalid = remote_bridge.dispatch_remote_request(
            meeting_transcription_control.STATUS_OPERATION,
            {"meeting": "not-a-public-id"},
            "synthetic-paired-app-token-for-v1880",
        )
        assert invalid["status"] == 422 and invalid["ok"] is False

        print("Phase 18.8 meeting production control contract passed.")
    finally:
        meeting_transcription_remote._identity = original_identity
        meeting_transcription_remote._claimed_cloud_ready = original_claimed
        meeting_transcription.status = original_status
        meeting_transcription._JOBS.clear()

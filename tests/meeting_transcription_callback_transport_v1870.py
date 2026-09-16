from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-meeting-callback-v1870-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    from app.services import meeting_transcription

    callback_token = "v1850.1999999999." + ("c" * 64)
    job = meeting_transcription._MeetingJob(
        idempotency_key="vp3-meeting-transcription:" + ("a" * 32),
        public_id="a" * 32,
        room_name="vp3-meeting-e2e-aabbcc",
        title="Phase 18.7 callback transport",
        app_key="vp3-phase-18-7",
        livekit_url="wss://example.livekit.cloud",
        livekit_identity="vp3-homeserver-" + ("b" * 24),
        livekit_token=("h" * 24) + "." + ("p" * 24) + "." + ("s" * 24),
        callback_url="https://vp3.me/api/video-meeting-worker.php",
        callback_token=callback_token,
        callback_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        language="en",
    )
    body = {
        "meeting": job.public_id,
        "room_name": job.room_name,
        "participant_identity": "user-alice",
        "speaker_name": "Alice",
        "start_ms": 100,
        "end_ms": 1100,
        "text": "Private local transcript from HomeServer",
        "confidence": None,
        "source": "homeserver",
        "source_key": "hs-test-source-key",
        "is_final": True,
    }

    captured: dict = {}
    original_client = meeting_transcription.httpx.Client

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    try:
        meeting_transcription.httpx.Client = FakeClient
        meeting_transcription._post_callback(job, body)
    finally:
        meeting_transcription.httpx.Client = original_client

    assert captured["url"] == "https://vp3.me/api/video-meeting-worker.php"
    assert captured["json"] == body
    assert captured["headers"]["Authorization"] == f"Bearer {callback_token}"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["User-Agent"].startswith("HomeServer-Meeting-STT/")
    assert captured["client_kwargs"]["follow_redirects"] is False
    assert captured["client_kwargs"]["trust_env"] is False
    assert callback_token not in str(body)
    assert job.livekit_token not in str(body)

print("Phase 18.7 HomeServer callback transport contract passed.")

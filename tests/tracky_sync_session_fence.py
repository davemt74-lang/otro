"""Late supplementary HTTPS responses cannot mutate a replacement pairing."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="tracky-sync-session-fence-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
    from app.database import db, initialize_database
    from app.services import tracky_physical_context as physical
    from app.services.https_bridge_session import load_https_session, save_https_session

    initialize_database()
    physical.ingest_semantic_projection({"events": [{"event_id": "session-fence-event", "sequence": 1,
        "event_type": "object.moved", "severity": "notable", "confidence": .9,
        "privacy_class": "cloud_derived", "occurred_at": "2026-11-23T02:15:00+00:00",
        "summary": "Synthetic regression event", "subject": {"entity_id": "object:keys", "type": "object"}}],
        "world_state": [], "context": {}, "context_sequence": 1}, source="bridge-session-regression")
    endpoint = "https://cloud.invalid/api/homeserver-https-poll-v1300.php"
    original_token, new_token = "S" * 64, "N" * 64
    active = True
    mode = "success"

    class Response:
        status_code = 200
        def json(self):
            # This malformed mirror would fail if an obsolete response were
            # ingested. The current pairing's local state must remain untouched.
            result = {"ok": True, "last_sequence": 999, "cursor": "obsolete-cloud-cursor"}
            if mode != "current": result["identity_continuity"] = {"protocol": "invalid"}
            return result

    class HTTPS:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, *, json, headers):
            global active
            assert headers["Authorization"] == "Bearer " + original_token
            if mode != "current":
                if mode == "stopped":
                    active = False
                else:
                    # Real concurrent re-pairing must be possible while HTTP is
                    # pending; the sender cannot hold SESSION_LOCK across it.
                    replacement = threading.Thread(target=save_https_session, args=(endpoint, new_token))
                    replacement.start()
                    replacement.join(2)
                    assert not replacement.is_alive(), "Pairing lock held across HTTPS"
                with db() as connection:
                    connection.execute("UPDATE tracky_cloud_sync_state SET last_error='new session warning',sync_cursor='current-cursor',last_sequence=17 WHERE id=1")
            if mode == "http-error":
                raise physical.httpx.ReadTimeout("Synthetic old-session timeout")
            if mode == "rejected":
                response = Response()
                response.status_code = 503
                return response
            return Response()

    for mode in ("success", "http-error", "rejected", "stopped"):
        save_https_session(endpoint, original_token)
        active = True
        with patch.object(physical.httpx, "Client", HTTPS):
            reply = physical.sync_cloud(force=True, active=lambda: active)
        assert reply["deferred"] and reply["reason"] == "session_changed_or_stopped", reply
        with db() as connection:
            state = dict(connection.execute("SELECT * FROM tracky_cloud_sync_state WHERE id=1").fetchone())
            event = connection.execute("SELECT cloud_synced FROM tracky_physical_events WHERE event_id='session-fence-event'").fetchone()
        assert state["last_error"] == "new session warning", state
        assert state["sync_cursor"] == "current-cursor" and state["last_sequence"] == 17
        assert not event["cloud_synced"]
        assert load_https_session()["session_token"] == (original_token if mode == "stopped" else new_token)
    print("PASS late success, HTTP error, rejection and stopped sync preserve current-session mirrors, cursor and warnings")

    mode = "current"
    active = True
    save_https_session(endpoint, original_token)
    with patch.object(physical.httpx, "Client", HTTPS):
        reply = physical.sync_cloud(force=True, active=lambda: active)
    assert reply["ok"] and reply["synced_events"] >= 1
    with db() as connection:
        state = dict(connection.execute("SELECT * FROM tracky_cloud_sync_state WHERE id=1").fetchone())
        event = connection.execute("SELECT cloud_synced FROM tracky_physical_events WHERE event_id='session-fence-event'").fetchone()
    assert state["sync_cursor"] == "obsolete-cloud-cursor" and not state["last_error"]
    assert event["cloud_synced"]
    print("PASS current authenticated supplementary sync still applies receipts normally")

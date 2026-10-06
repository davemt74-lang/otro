from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from contextlib import nullcontext
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def capture_error(call):
    try:
        call()
    except sqlite3.OperationalError as exc:
        return exc
    raise AssertionError("Expected a real SQLite OperationalError")


with tempfile.TemporaryDirectory(prefix="bridge-sqlite-recovery-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    from app.database import initialize_database
    from app.config import settings
    from app.services import remote_bridge as bridge, pairing
    from app.services.https_bridge_session import load_https_session, save_https_session
    initialize_database()

    # Obtain genuine SQLite error codes, not errors manufactured by a mock.
    busy_path = Path(directory) / "busy-fixture.db"
    a = sqlite3.connect(busy_path)
    b = sqlite3.connect(busy_path, timeout=0)
    a.execute("CREATE TABLE fixture(value TEXT)")
    a.commit()
    a.execute("BEGIN IMMEDIATE")
    busy = capture_error(lambda: b.execute("INSERT INTO fixture VALUES ('x')"))
    a.rollback()
    a.close()
    b.close()
    assert busy.sqlite_errorcode == sqlite3.SQLITE_BUSY

    read_only = sqlite3.connect(busy_path.as_uri() + "?mode=ro", uri=True)
    readonly = capture_error(lambda: read_only.execute("INSERT INTO fixture VALUES ('x')"))
    read_only.close()
    cannot_open = capture_error(lambda: sqlite3.connect(Path(directory) / "absent" / "private-key.db"))
    full = sqlite3.connect(Path(directory) / "full-fixture.db")
    full.execute("CREATE TABLE fixture(value BLOB)")
    full.execute("PRAGMA max_page_count=2")
    full_error = capture_error(lambda: full.execute("INSERT INTO fixture VALUES (zeroblob(20000))"))
    full.close()
    query = sqlite3.connect(":memory:")
    schema_error = capture_error(lambda: query.execute("SELECT * FROM secret_pairing_token_table"))
    query.close()
    for exc, expected in [(busy, "SQLITE_BUSY"), (readonly, "SQLITE_READONLY"),
                          (cannot_open, "SQLITE_CANTOPEN"), (full_error, "SQLITE_FULL"),
                          (schema_error, "SQLITE_ERROR")]:
        detail = bridge._database_error_details(exc)
        assert detail["sqlite_name"] == expected, detail
        assert "secret_pairing_token_table" not in json.dumps(detail)
        assert "private-key.db" not in json.dumps(detail)
    print("PASS real SQLite errors report safe lock, access, full and schema diagnostics")

    request = pairing.create_pairing_request("vp3", "VP3", ["agent.chat"])
    pairing.approve_pairing_request(request["request_id"])
    local_token = request["claim_token"]
    session_token = "S" * 64
    endpoint = "https://vp3.me/api/homeserver-https-poll-v1300.php"
    save_https_session(endpoint, session_token)
    bridge.save_vp3_https_settings(endpoint, True)
    secret_before = settings.remote_https_session_path.read_bytes()
    settings_before = bridge.get_bridge_settings()

    def exercise(*, fault_before_dispatch=False, fault_during_dispatch=False, network_failure=False):
        bridge._RELOAD_EVENT.clear()
        bridge._set_state(running=False, connected=False, stage="stopped", last_error=None)
        worker = bridge.RemoteBridgeWorker()
        posts, waits, actions = [], [], []
        touched = 0
        network_failed = False
        original_touch = bridge.touch_paired_app
        original_disconnect = bridge.federated_data.note_peer_disconnected

        class Stop:
            stopped = False
            def is_set(self):
                return self.stopped
            def set(self):
                self.stopped = True
            def wait(self, seconds):
                waits.append((seconds, dict(bridge._STATE)))
                assert len(waits) < 20, "Recovery loop did not converge"
                return self.stopped

        worker._stop = Stop()
        worker._wait_local_api = lambda: True

        class Response:
            status_code = 200
            def __init__(self, requests):
                self.requests = requests
            def json(self):
                return {"ok": True, "requests": self.requests, "poll_after_ms": 250}

        class Client:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def post(self, url, **kwargs):
                nonlocal network_failed
                assert url == endpoint
                assert kwargs["headers"]["Authorization"] == "Bearer " + session_token
                sent = json.loads(json.dumps(kwargs["json"]))
                posts.append(sent)
                if len(posts) == 1:
                    return Response([{"request_id": "sqlite-ping", "operation": "system.ping",
                                      "payload": {"nonce": "once"}, "bearer_token": local_token}])
                assert len(actions) == 1, actions
                assert len(sent["results"]) == 1, sent
                if network_failure and not network_failed:
                    network_failed = True
                    raise bridge.httpx.ConnectError("synthetic interrupted response")
                worker._stop.set()
                return Response([])

        def touch(app_key):
            nonlocal touched
            touched += 1
            if fault_before_dispatch and touched == 1:
                raise busy
            return original_touch(app_key)

        def disconnect(peer, message):
            if "SQLITE_BUSY" in message:
                # Error reporting also encounters the unavailable database.
                raise busy
            return original_disconnect(peer, message)

        def dispatch(operation, payload, token):
            if operation == "capabilities":
                return {"ok": True, "payload": {"version": "2.4"}}
            assert operation == "system.ping" and token == local_token
            assert pairing.authenticate(token)["app_key"] == "vp3"
            actions.append(payload)
            if fault_during_dispatch:
                raise busy
            return {"ok": True, "status": 200, "payload": {"pong": True}}

        with patch.object(bridge.httpx, "Client", Client), \
             patch.object(bridge, "touch_paired_app", touch), \
             patch.object(bridge.federated_data, "note_peer_disconnected", disconnect), \
             patch.object(bridge, "dispatch_remote_request", dispatch), \
             patch.object(bridge.tracky_physical_context, "sync_due", lambda: False):
            worker._run_guarded()
        assert len(actions) == 1
        assert len(posts) == (3 if network_failure else 2)
        assert posts[-1]["results"][0]["ok"] is not fault_during_dispatch
        if fault_during_dispatch:
            assert posts[-1]["results"][0]["status"] == 503
        if fault_before_dispatch:
            retry = [state for _, state in waits if state["stage"] == "database-retrying"]
            assert retry and retry[0]["running"] and not retry[0]["connected"]
            assert retry[0]["last_error"].startswith("SQLITE_BUSY:")
        if network_failure:
            assert posts[-2]["results"] == posts[-1]["results"]
        assert load_https_session()["session_token"] == session_token
        assert settings.remote_https_session_path.read_bytes() == secret_before
        assert bridge.get_bridge_settings() == settings_before
        assert pairing.authenticate(local_token)["app_key"] == "vp3"

    exercise(fault_before_dispatch=True)
    print("PASS SQLite interruption retries, retains accepted Cloud request and executes once")
    exercise(network_failure=True)
    print("PASS interrupted exchange retains result receipt without replaying completed action")
    exercise(fault_during_dispatch=True)
    print("PASS database failure during action produces a failure receipt without automatic replay")

    # Persistent errors back off to 30 seconds and remain interruptible by Stop.
    worker = bridge.RemoteBridgeWorker()
    waits = []
    class PersistentStop:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self, seconds):
            waits.append(seconds)
            if len(waits) == 8: self.stopped = True
            return self.stopped
    worker._stop = PersistentStop()
    worker._run = lambda: (_ for _ in ()).throw(readonly)
    with patch.object(bridge.federated_data, "note_peer_disconnected", lambda *a: (_ for _ in ()).throw(readonly)):
        worker._run_guarded()
    assert waits == [1, 2, 4, 8, 16, 30, 30, 30], waits
    assert "SQLITE_READONLY" in bridge._STATE["last_error"]
    assert load_https_session()["session_token"] == session_token
    print("PASS persistent database failure uses bounded interruptible retry and preserves pairing")

    # A replacement session cannot inherit requests or receipts from the old one.
    worker = bridge.RemoteBridgeWorker()
    worker._https_session_token = session_token
    worker._https_pending_results = [{"request_id": "old-result"}]
    worker._https_pending_exchange = {"ok": True, "requests": [{"request_id": "old-request"}]}
    save_https_session(endpoint, "N" * 64)
    worker._stop = PersistentStop()
    def receive(*args):
        assert worker._https_pending_results == []
        assert worker._https_pending_exchange is None
        worker._stop.stopped = True
    with patch.object(worker, "_receive_https_exchange", receive), \
         patch.object(bridge.httpx, "Client", lambda *a, **kw: nullcontext(None)), \
         patch.object(bridge, "dispatch_remote_request", lambda *a: {"ok": True, "payload": {}}):
        worker._run_https(bridge.get_bridge_settings())
    assert load_https_session()["session_token"] == "N" * 64
    print("PASS replacement pairing drops old-session requests and receipts")

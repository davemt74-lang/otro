"""Real API, relay and SQLite regression: feature faults cannot stop the bridge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

ROOT = Path(__file__).resolve().parents[1]
SESSION_TOKEN = "S" * 64
state = {"requests": [], "results": [], "polls": 0, "capabilities": {}, "sync_fail": True, "workspace_failures": 0}
lock = threading.Lock()


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer " + SESSION_TOKEN
        assert self.headers["X-HomeServer-Device"]
        with lock:
            if self.path == "/poll":
                assert self.headers["X-VP3-HomeServer-Session"] == SESSION_TOKEN
                state["polls"] += 1
                state["capabilities"] = body["capabilities"]
                state["results"].extend(body.get("results", []))
                reply = {"ok": True, "requests": state["requests"], "poll_after_ms": 250}
                state["requests"] = []
                status = 200
            elif self.path == "/api/tracky-sync-v270.php":
                status = 503 if state["sync_fail"] else 200
                reply = {"ok": status == 200}
            elif self.path == "/homeserver-workspace-sync-v1.php":
                state["workspace_failures"] += 1
                status = 503
                reply = {"ok": False, "error": "Workspace service intentionally unavailable"}
            else:
                raise AssertionError("Unexpected relay route: " + self.path)
        encoded = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def wait_until(fn, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (httpx.HTTPError, KeyError):
            pass
        time.sleep(0.1)
    raise AssertionError("Connection lifecycle deadline exceeded")


RUNTIME = r'''
import json, os
import uvicorn
from app.config import settings
object.__setattr__(settings, "port", int(os.environ["BRIDGE_TEST_PORT"]))
from app.database import initialize_database
from app.services.knowledge import ensure_knowledge_index
from app.services import pairing, remote_bridge
from app.services.https_bridge_session import save_https_session
initialize_database()
ensure_knowledge_index()
p = pairing.create_pairing_request("vp3", "VP3", ["memory.read", "events.read"])
pairing.approve_pairing_request(p["request_id"])
endpoint = os.environ["BRIDGE_TEST_RELAY"]
save_https_session(endpoint, "S" * 64)
remote_bridge.save_vp3_https_settings(endpoint, True)
from app.security import OWNER_CONTROL_TOKEN
(settings.data_dir / "fixture.json").write_text(json.dumps({"token": p["claim_token"], "owner": OWNER_CONTROL_TOKEN}), encoding="utf-8")
from app.runtime import app
worker = remote_bridge.RemoteBridgeWorker()
worker.start()
try:
    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port,
                                 log_level="critical", access_log=False)).run()
finally:
    worker.stop()
'''


def main():
    with tempfile.TemporaryDirectory(prefix="bridge-feature-isolation-") as directory:
        data = Path(directory)
        relay = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
        threading.Thread(target=relay.serve_forever, daemon=True).start()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {key: value for key, value in os.environ.items()
               if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
        env.update(HOMESERVER_DATA_DIR=directory, BRIDGE_TEST_PORT=str(port),
                   BRIDGE_TEST_RELAY=f"http://127.0.0.1:{relay.server_port}/poll")
        with (data / "runtime.log").open("wb") as log, \
             httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3) as client:
            process = subprocess.Popen([sys.executable, "-c", RUNTIME], cwd=ROOT,
                                       env=env, stdout=log, stderr=log)
            try:
                wait_until(lambda: client.get("/api/v1/health").status_code == 200)
                fixture = json.loads((data / "fixture.json").read_text(encoding="utf-8"))
                secret = fixture["owner"]
                assert client.post("/__owner/session", headers={"x-homeserver-owner": secret}).status_code == 200
                token = fixture["token"]
                session = data / "security/vp3-https-session.dat"
                original_session = session.read_bytes()

                def status():
                    response = client.get("/api/v1/control/cloud-connection")
                    assert response.status_code == 200
                    return response.json()["cloud"]

                def ping():
                    import uuid
                    request_id = str(uuid.uuid4())
                    with lock:
                        state["requests"].append({"request_id": request_id, "operation": "system.ping",
                                                  "payload": {"nonce": request_id}, "bearer_token": token})
                    receipt = wait_until(lambda: next((r for r in state["results"]
                                                       if r.get("request_id") == request_id), None))
                    assert receipt["ok"] and receipt["payload"]["pong"], receipt
                    assert receipt["payload"]["echo"] == request_id, receipt
                    assert sum(r.get("request_id") == request_id for r in state["results"]) == 1

                wait_until(lambda: status()["connected"])
                wait_until(lambda: state["workspace_failures"] > 0)
                ping()
                assert status()["connected"] and not status()["last_error"]
                print("PASS workspace HTTP failure preserves the live relay and authenticated ping", flush=True)
                with sqlite3.connect(data / "homeserver.db") as db:
                    db.execute("ALTER TABLE tracky_cloud_sync_state RENAME TO unavailable_sync_state")
                wait_until(lambda: status()["feature_sync_error"].startswith("SQLITE_ERROR:"))
                polls = state["polls"]
                wait_until(lambda: state["polls"] >= polls + 3)
                assert status()["connected"] and not status()["last_error"]
                assert state["capabilities"] == {}, "Unavailable feature capabilities must fail closed"
                ping()
                with sqlite3.connect(data / "homeserver.db") as db:
                    db.execute("ALTER TABLE unavailable_sync_state RENAME TO tracky_cloud_sync_state")
                wait_until(lambda: not status()["feature_sync_error"])
                wait_until(lambda: bool(state["capabilities"]))
                print("PASS unavailable feature table preserves heartbeats, authenticated commands and receipts", flush=True)

                # Insert real pending feature data, then use a real HTTP 503.
                seed = r'''
from app.services import tracky_physical_context as ctx
ctx.ingest_semantic_projection({"events": [{"event_id": "bridge-sync-fixture", "sequence": 1,
    "event_type": "object.moved", "severity": "notable", "confidence": .9,
    "privacy_class": "cloud_derived", "occurred_at": "2026-10-06T02:15:00+00:00",
    "summary": "Synthetic regression event", "subject": {"entity_id": "object:keys", "type": "object"}}],
    "world_state": [], "context": {}, "context_sequence": 1}, source="bridge-regression")
'''
                subprocess.run([sys.executable, "-c", seed], cwd=ROOT, env=env, check=True,
                               stdout=log, stderr=log)
                wait_until(lambda: status()["feature_sync_error"])
                polls = state["polls"]
                wait_until(lambda: state["polls"] >= polls + 4)
                assert status()["connected"] and status()["feature_sync_error"]
                ping()
                with lock:
                    state["sync_fail"] = False
                wait_until(lambda: not status()["feature_sync_error"])
                with sqlite3.connect(data / "homeserver.db") as db:
                    assert db.execute("SELECT cloud_synced FROM tracky_physical_events WHERE event_id='bridge-sync-fixture'").fetchone()[0] == 1
                    saved_hash = db.execute("SELECT token_hash FROM paired_apps WHERE app_key='vp3'").fetchone()[0]
                assert saved_hash == hashlib.sha256(token.encode()).hexdigest()
                assert session.read_bytes() == original_session
                assert status()["paired"] and status()["connected"]
                print("PASS feature HTTP failure retains warning during backoff and resumes synchronization", flush=True)
                print("PASS saved pairing and session remain unchanged across both faults", flush=True)
            except Exception:
                log.flush()
                print((data / "runtime.log").read_text(errors="replace"), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                relay.shutdown()
                relay.server_close()


if __name__ == "__main__":
    main()

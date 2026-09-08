from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

from websockets.sync.server import serve

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-remote-socket-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import initialize_database  # noqa: E402
    from app.services import remote_bridge  # noqa: E402

    initialize_database()

    listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listening.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listening.bind(("127.0.0.1", 0))
    listening.listen()
    port = listening.getsockname()[1]
    completed = threading.Event()
    release_broker = threading.Event()
    captured: dict = {}
    synthetic_token = "synthetic_socket_token_" + "s" * 36

    def handler(websocket):
        hello = json.loads(websocket.recv(timeout=5))
        captured["hello"] = hello
        websocket.send(json.dumps({
            "type": "hello.ok",
            "claimed": False,
            "claim_code": "TEST-4821",
            "connection_id": "conn-synthetic-1",
        }))
        websocket.send(json.dumps({
            "type": "request",
            "request_id": "request-1",
            "operation": "chat",
            "bearer_token": synthetic_token,
            "payload": {"message": "synthetic relay payload"},
        }))
        captured["response"] = json.loads(websocket.recv(timeout=5))
        completed.set()
        release_broker.wait(5)

    server = serve(
        handler,
        sock=listening,
        subprotocols=["homeserver.bridge.v1"],
        ping_interval=None,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    original_dispatch = remote_bridge.dispatch_remote_request
    remote_bridge.dispatch_remote_request = lambda operation, payload, bearer_token=None: {
        "status": 200,
        "ok": True,
        "payload": {
            "operation": operation,
            "bearer_present": bool(bearer_token),
            "message_length": len(str(payload.get("message") or "")),
        },
    }

    worker = remote_bridge.RemoteBridgeWorker()
    worker._wait_local_api = lambda: True
    try:
        remote_bridge.save_bridge_settings(True, f"ws://127.0.0.1:{port}/bridge")
        worker.start()
        assert completed.wait(8), "Remote bridge did not complete loopback relay exchange"
        assert captured["hello"]["type"] == "hello"
        assert captured["hello"]["protocol"] == "homeserver-relay-v1"
        assert captured["hello"]["version"] == "0.18.0"
        response = captured["response"]
        assert response["type"] == "response"
        assert response["request_id"] == "request-1"
        assert response["ok"] is True
        assert response["payload"]["operation"] == "chat"
        assert response["payload"]["bearer_present"] is True

        status = remote_bridge.bridge_status()
        assert status["runtime"]["connected"] is True
        assert status["runtime"]["claimed"] is False
        assert status["runtime"]["claim_code"] == "TEST-4821"
        audit_blob = json.dumps(remote_bridge.list_bridge_events(100), ensure_ascii=False)
        assert synthetic_token not in audit_blob
        assert "synthetic relay payload" not in audit_blob
        assert "chat" in audit_blob
    finally:
        release_broker.set()
        worker.stop()
        remote_bridge.dispatch_remote_request = original_dispatch
        server.shutdown()
        server_thread.join(timeout=3)

print("HomeServer outbound websocket bridge test passed")

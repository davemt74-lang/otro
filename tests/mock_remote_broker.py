from __future__ import annotations

import json
import os
from pathlib import Path

from websockets.sync.server import serve


port = int(os.environ.get("HOMESERVER_REMOTE_TEST_PORT", "48765"))
marker_raw = os.environ.get("HOMESERVER_REMOTE_MARKER")
token_file_raw = os.environ.get("HOMESERVER_REMOTE_TOKEN_FILE")
if not marker_raw:
    raise SystemExit("HOMESERVER_REMOTE_MARKER is required")
if not token_file_raw:
    raise SystemExit("HOMESERVER_REMOTE_TOKEN_FILE is required")
marker = Path(marker_raw)
token_file = Path(token_file_raw)


def handler(websocket) -> None:
    hello = json.loads(websocket.recv(timeout=15))
    assert hello["type"] == "hello"
    assert hello["protocol"] == "homeserver-relay-v1"
    assert hello["version"] == "0.12.0"
    assert str(hello["device_id"]).startswith("hs-")

    websocket.send(json.dumps({
        "type": "hello.ok",
        "claimed": True,
        "connection_id": "packaged-loopback-1",
    }))

    websocket.send(json.dumps({
        "type": "request",
        "request_id": "packaged-capabilities-1",
        "operation": "capabilities",
        "payload": {},
    }))
    capabilities = json.loads(websocket.recv(timeout=15))
    assert capabilities["type"] == "response"
    assert capabilities["request_id"] == "packaged-capabilities-1"
    assert capabilities["ok"] is True
    assert capabilities["status"] == 200
    assert capabilities["payload"]["version"] == "0.12.0"
    assert "remote.bridge.v1" in capabilities["payload"]["features"]

    token = token_file.read_text(encoding="utf-8").strip()
    assert len(token) >= 20

    websocket.send(json.dumps({
        "type": "request",
        "request_id": "packaged-memory-read-1",
        "operation": "memory.read",
        "bearer_token": token,
        "payload": {},
    }))
    allowed = json.loads(websocket.recv(timeout=15))
    assert allowed["type"] == "response"
    assert allowed["request_id"] == "packaged-memory-read-1"
    assert allowed["ok"] is True
    assert allowed["status"] == 200
    assert allowed["payload"]["app"] == "remote-packaged"
    assert any(
        item.get("content") == "PACKAGED_REMOTE_PRIVATE_MEMORY_58241"
        for item in allowed["payload"].get("items", [])
    )

    websocket.send(json.dumps({
        "type": "request",
        "request_id": "packaged-contacts-denied-1",
        "operation": "contacts.search",
        "bearer_token": token,
        "payload": {"query": "synthetic"},
    }))
    denied = json.loads(websocket.recv(timeout=15))
    assert denied["type"] == "response"
    assert denied["request_id"] == "packaged-contacts-denied-1"
    assert denied["ok"] is False
    assert denied["status"] == 403

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "ok": True,
        "device_id": hello["device_id"],
        "response_status": capabilities["status"],
        "allowed_status": allowed["status"],
        "denied_status": denied["status"],
    }), encoding="utf-8")


with serve(
    handler,
    "127.0.0.1",
    port,
    subprotocols=["homeserver.bridge.v1"],
    ping_interval=None,
):
    import threading
    threading.Event().wait()

from __future__ import annotations

import json
import os
from pathlib import Path

from websockets.sync.server import serve


port = int(os.environ.get("HOMESERVER_REMOTE_TEST_PORT", "48765"))
marker_raw = os.environ.get("HOMESERVER_REMOTE_MARKER")
if not marker_raw:
    raise SystemExit("HOMESERVER_REMOTE_MARKER is required")
marker = Path(marker_raw)


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
    response = json.loads(websocket.recv(timeout=15))
    assert response["type"] == "response"
    assert response["request_id"] == "packaged-capabilities-1"
    assert response["ok"] is True
    assert response["status"] == 200
    assert response["payload"]["version"] == "0.12.0"
    assert "remote.bridge.v1" in response["payload"]["features"]

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "ok": True,
        "device_id": hello["device_id"],
        "response_status": response["status"],
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

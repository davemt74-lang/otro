from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from websockets.sync.server import serve


port = int(os.environ.get("HOMESERVER_REMOTE_TEST_PORT", "48765"))
marker_raw = os.environ.get("HOMESERVER_REMOTE_MARKER")
token_file_raw = os.environ.get("HOMESERVER_REMOTE_TOKEN_FILE")
progress_raw = os.environ.get("HOMESERVER_REMOTE_PROGRESS")
if not marker_raw:
    raise SystemExit("HOMESERVER_REMOTE_MARKER is required")
if not token_file_raw:
    raise SystemExit("HOMESERVER_REMOTE_TOKEN_FILE is required")
if not progress_raw:
    raise SystemExit("HOMESERVER_REMOTE_PROGRESS is required")
marker = Path(marker_raw)
token_file = Path(token_file_raw)
progress = Path(progress_raw)


def publish_progress(stage: str) -> None:
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(stage, encoding="utf-8")


def publish_result(payload: dict) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def handler(websocket) -> None:
    result: dict = {"ok": False, "stage": "accepted"}
    try:
        publish_progress("accepted")
        hello = json.loads(websocket.recv(timeout=15))
        result.update({
            "stage": "hello",
            "hello_type": hello.get("type"),
            "hello_protocol": hello.get("protocol"),
            "hello_version": hello.get("version"),
            "device_id_valid": str(hello.get("device_id") or "").startswith("hs-"),
        })
        publish_progress("hello")
        if result["hello_type"] != "hello" or result["hello_protocol"] != "homeserver-relay-v1":
            raise AssertionError("invalid HomeServer relay hello")
        if result["hello_version"] != "0.12.0" or not result["device_id_valid"]:
            raise AssertionError("invalid packaged HomeServer identity/version")

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
        result.update({
            "stage": "capabilities",
            "response_status": capabilities.get("status"),
            "capabilities_ok": capabilities.get("ok") is True,
            "capabilities_request_id": capabilities.get("request_id"),
            "remote_feature": "remote.bridge.v1" in (capabilities.get("payload", {}).get("features") or []),
        })
        publish_progress("capabilities")

        token = token_file.read_text(encoding="utf-8").strip()
        if len(token) < 20:
            raise AssertionError("packaged paired-app token fixture is invalid")

        websocket.send(json.dumps({
            "type": "request",
            "request_id": "packaged-memory-read-1",
            "operation": "memory.read",
            "bearer_token": token,
            "payload": {},
        }))
        allowed = json.loads(websocket.recv(timeout=15))
        allowed_payload = allowed.get("payload") if isinstance(allowed.get("payload"), dict) else {}
        result.update({
            "stage": "memory.read",
            "allowed_status": allowed.get("status"),
            "allowed_ok": allowed.get("ok") is True,
            "allowed_request_id": allowed.get("request_id"),
            "allowed_app": allowed_payload.get("app"),
            "allowed_private_present": any(
                isinstance(item, dict) and item.get("content") == "PACKAGED_REMOTE_PRIVATE_MEMORY_58241"
                for item in (allowed_payload.get("items") or [])
            ),
        })
        publish_progress("memory.read")

        websocket.send(json.dumps({
            "type": "request",
            "request_id": "packaged-contacts-denied-1",
            "operation": "contacts.search",
            "bearer_token": token,
            "payload": {"query": "synthetic"},
        }))
        denied = json.loads(websocket.recv(timeout=15))
        result.update({
            "stage": "contacts.search",
            "denied_status": denied.get("status"),
            "denied_ok": denied.get("ok") is False,
            "denied_request_id": denied.get("request_id"),
        })
        publish_progress("contacts.search")

        result["ok"] = all((
            result.get("capabilities_ok") is True,
            result.get("response_status") == 200,
            result.get("capabilities_request_id") == "packaged-capabilities-1",
            result.get("remote_feature") is True,
            result.get("allowed_ok") is True,
            result.get("allowed_status") == 200,
            result.get("allowed_request_id") == "packaged-memory-read-1",
            result.get("allowed_app") == "remote-packaged",
            result.get("allowed_private_present") is True,
            result.get("denied_ok") is True,
            result.get("denied_status") == 403,
            result.get("denied_request_id") == "packaged-contacts-denied-1",
        ))
        result["stage"] = "complete"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:300]
    finally:
        publish_result(result)


with serve(
    handler,
    "127.0.0.1",
    port,
    subprotocols=["homeserver.bridge.v1"],
    ping_interval=None,
):
    publish_progress("listening")
    threading.Event().wait()

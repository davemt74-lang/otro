from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

port = int(os.environ.get("HOMESERVER_HTTPS_RECONNECT_PORT", "48766"))
config_path = Path(os.environ["HOMESERVER_HTTPS_RECONNECT_CONFIG"])
marker_path = Path(os.environ["HOMESERVER_HTTPS_RECONNECT_MARKER"])
progress_path = Path(os.environ["HOMESERVER_HTTPS_RECONNECT_PROGRESS"])
restart_flag = Path(os.environ["HOMESERVER_HTTPS_RECONNECT_RESTART_FLAG"])

config = json.loads(config_path.read_text(encoding="utf-8"))
local_token = str(config["local_token"])
session_token = str(config["session_token"])

state = {
    "stage": "send_ping",
    "device_id": None,
    "polls": 0,
    "ping_verified": False,
    "shared_verified": False,
}


def progress(stage: str) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(stage, encoding="utf-8")


def finish(payload: dict) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def find_result(results: list, request_id: str) -> dict | None:
    for item in results:
        if isinstance(item, dict) and item.get("request_id") == request_id:
            return item
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            if self.path != "/poll":
                raise AssertionError(f"unexpected path: {self.path}")

            auth = self.headers.get("Authorization", "")
            session_header = self.headers.get("X-VP3-HomeServer-Session", "")
            device_id = self.headers.get("X-HomeServer-Device", "")
            if auth != f"Bearer {session_token}" or session_header != session_token:
                raise AssertionError("saved VP3 HTTPS session token was not reused")
            if not device_id.startswith("hs-"):
                raise AssertionError("HomeServer device identity missing")

            if state["device_id"] is None:
                state["device_id"] = device_id
            elif state["device_id"] != device_id:
                raise AssertionError("HomeServer device identity changed across restart")

            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if payload.get("version") != "2.3":
                raise AssertionError("unexpected HomeServer version")
            if not isinstance(payload.get("capabilities"), dict):
                raise AssertionError("capabilities were not advertised")

            results = payload.get("results") if isinstance(payload.get("results"), list) else []
            state["polls"] += 1
            requests: list[dict] = []

            if state["stage"] == "send_ping":
                requests.append(
                    {
                        "request_id": "restart-ping-1",
                        "operation": "system.ping",
                        "bearer_token": local_token,
                        "payload": {"nonce": "restart-proof-v22"},
                    }
                )
                state["stage"] = "await_ping_result"
                progress("ping-sent")

            elif state["stage"] == "await_ping_result":
                result = find_result(results, "restart-ping-1")
                if result is not None:
                    body = result.get("payload") if isinstance(result.get("payload"), dict) else {}
                    if result.get("ok") is not True or body.get("pong") is not True:
                        raise AssertionError("system.ping result was not successful")
                    if body.get("echo") != "restart-proof-v22" or body.get("version") != "2.3":
                        raise AssertionError("system.ping round-trip proof was invalid")
                    if body.get("app") != "vp3":
                        raise AssertionError("system.ping did not authenticate as VP3")
                    state["ping_verified"] = True
                    state["stage"] = "wait_restart"
                    progress("ping-complete")

            elif state["stage"] == "wait_restart":
                if restart_flag.is_file():
                    requests.append(
                        {
                            "request_id": "restart-shared-1",
                            "operation": "shared.context.exchange",
                            "bearer_token": local_token,
                            "payload": {
                                "query": "restart",
                                "cloud_snapshot": {
                                    "version": "2.2",
                                    "revision": "restart-cloud-revision-1",
                                    "generated_at": "2026-09-24T23:00:00Z",
                                    "datasets": {
                                        "memory": [
                                            {
                                                "id": "restart-memory-1",
                                                "title": "Restart memory",
                                                "content": "Cloud context survived HomeServer restart",
                                                "updated_at": "2026-09-24T23:00:00Z",
                                            }
                                        ],
                                        "knowledge": [],
                                        "contacts": [],
                                        "tasks": [],
                                        "calendar": [],
                                        "files": [],
                                        "notifications": [],
                                    },
                                },
                            },
                        }
                    )
                    state["stage"] = "await_shared_result"
                    progress("shared-sent")

            elif state["stage"] == "await_shared_result":
                result = find_result(results, "restart-shared-1")
                if result is not None:
                    body = result.get("payload") if isinstance(result.get("payload"), dict) else {}
                    cloud = body.get("cloud_mirror") if isinstance(body.get("cloud_mirror"), dict) else {}
                    home = body.get("homeserver_snapshot") if isinstance(body.get("homeserver_snapshot"), dict) else {}
                    datasets = home.get("datasets") if isinstance(home.get("datasets"), dict) else {}
                    expected = {"memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications"}
                    if result.get("ok") is not True or body.get("version") != "2.2":
                        raise AssertionError("shared context exchange failed after restart")
                    if cloud.get("revision") != "restart-cloud-revision-1":
                        raise AssertionError("Cloud mirror was not restored after restart")
                    if home.get("authoritative_source") != "homeserver" or set(datasets) != expected:
                        raise AssertionError("HomeServer snapshot was incomplete after restart")
                    state["shared_verified"] = True
                    state["stage"] = "complete"
                    progress("complete")
                    finish(
                        {
                            "ok": True,
                            "stage": "complete",
                            "ping_verified": state["ping_verified"],
                            "shared_verified": state["shared_verified"],
                            "device_id": state["device_id"],
                            "polls": state["polls"],
                        }
                    )

            self._send({"ok": True, "poll_after_ms": 250, "requests": requests})
        except Exception as exc:
            progress("failed")
            finish(
                {
                    "ok": False,
                    "stage": state.get("stage"),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:400],
                    "polls": state.get("polls", 0),
                }
            )
            self._send({"ok": False, "error": "fixture failure"}, status=500)


progress("listening")
server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
server.serve_forever()

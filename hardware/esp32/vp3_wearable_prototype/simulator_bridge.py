#!/usr/bin/env python3
"""Local-only VP3 wearable simulator. Never connects to production services."""
from __future__ import annotations

import json
import sys
import threading
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dev_voice_preview import VoicePreview

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
PROTOCOL = "vp3-hw-v1"
CONTROLLER = "vp3-wearable-waveshare-146-sim"
MODES = frozenset(
    {"off", "idle", "listening", "thinking", "speaking", "privacy", "error", "updating"}
)
ACTIONS = frozenset({"press", "release", "hold"})


class SimulatedDevice:
    """In-memory wire fixture, not an authenticated HomeServer session."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.log = deque(maxlen=32)
        self.event_seq = 0
        self.state_seq = 0
        self.mode = "idle"
        self.pressed = False
        self.connected = False
        self.voice = VoicePreview()
        self.connect()

    def _record(self, direction: str, frame: dict) -> None:
        self.log.append({"direction": direction, "frame": frame})

    def _hello(self) -> dict:
        return {
            "type": "hello",
            "protocol": PROTOCOL,
            "controller_id": CONTROLLER,
            "firmware": "0.1.0-simulator",
            "hardware_revision": "waveshare-esp32-s3-touch-lcd-1.46",
            "components": ["agent_button", "status_light", "display"],
            "capabilities": ["agent_button", "status_light", "display"],
        }

    def _state(self) -> dict:
        self.state_seq += 1
        return {
            "type": "state",
            "seq": self.state_seq,
            "components": {
                "agent_button": {
                    "present": True, "ready": True, "pressed": self.pressed
                },
                "status_light": {"present": True, "ready": True},
                "display": {"present": True, "ready": True},
            },
        }

    def connect(self) -> None:
        with self.lock:
            self.connected = True
            self.event_seq = 0
            self.state_seq = 0
            self.pressed = False
            self.mode = "idle"
            self.voice.reset()
            self.log.clear()  # Re-handshake invalidates stale simulated events.
            self._record("home-to-device", {
                "type": "hello", "protocol": PROTOCOL, "client": "vp3-os", "version": "v0.20"
            })
            self._record("device-to-home", self._hello())
            self._record("device-to-home", self._state())

    def touch(self, action: str) -> dict:
        if action not in ACTIONS:
            raise ValueError("invalid_action")
        with self.lock:
            self.event_seq += 1
            self.pressed = action == "press" or (action == "hold" and self.pressed)
            event = {
                "type": "event", "seq": self.event_seq,
                "event": "agent_button", "action": action
            }
            self._record("device-to-home", event)
            self.voice.action(action)  # UI-only: never starts microphone capture.
            self._record("device-to-home", self._state())
            return event

    def set_mode(self, mode: str) -> dict:
        if mode not in MODES:
            raise ValueError("invalid_mode")
        with self.lock:
            self.mode = mode
            command_id = "sim-light-" + str(len(self.log) + 1)
            self._record("home-to-device", {
                "type": "command", "command_id": command_id,
                "command": "status_light.set", "args": {"mode": mode},
            })
            ack = {"type": "ack", "command_id": command_id, "ok": True}
            self._record("device-to-home", ack)
            return ack

    def voice_preview_command(self, action: str) -> dict:
        with self.lock:
            if action == "enable":
                self.voice.configure(True)
            elif action == "disable":
                self.voice.configure(False)
            elif action == "stop":
                self.voice.stop()
            else:
                raise ValueError("invalid_voice_preview_action")
            return self.voice.snapshot()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "connected": self.connected,
                "mode": self.mode,
                "pressed": self.pressed,
                "controller": CONTROLLER,
                "simulated": True,
                "microphone_enabled": False,
                "developer_voice": self.voice.snapshot(),
                "frames": list(self.log),
            }


DEVICE = SimulatedDevice()


class Handler(BaseHTTPRequestHandler):
    server_version = "VP3WearableSimulator/0.1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(200, DEVICE.snapshot())
        elif path in {"/", "/simulator.html", "/desktop_agent.html"}:
            page = "desktop_agent.html" if path == "/desktop_agent.html" else "simulator.html"
            body = (ROOT / page).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and origin not in {
            "http://127.0.0.1:8765", "http://localhost:8765"
        }:
            self._json(403, {"error": "invalid_origin"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._json(415, {"error": "expected_json"})
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self._json(400, {"error": "invalid_size"})
            return
        if size < 0 or size > 1024:
            self._json(413, {"error": "size_limit"})
            return
        try:
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("invalid_body")
            path = urlparse(self.path).path
            if path == "/api/touch":
                value = str(data.get("action", ""))
                result = DEVICE.touch(value)
            elif path == "/api/mode":
                value = str(data.get("mode", ""))
                result = DEVICE.set_mode(value)
            elif path == "/api/reconnect":
                DEVICE.connect()
                result = {"type": "hello", "ok": True}
            elif path == "/api/voice-preview":
                result = DEVICE.voice_preview_command(str(data.get("action", "")))
            else:
                self._json(404, {"error": "not_found"})
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)[:80]})
            return
        self._json(200, {"ok": True, "frame": result, "state": DEVICE.snapshot()})

    def log_message(self, format_string: str, *args: object) -> None:
        return  # Suppress request metadata; the simulator never stores user data.


def serve() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("VP3 wearable simulator: http://127.0.0.1:8765/")
    print("Local-only protocol test fixture; no microphone or HomeServer connection.")
    if "--open" in sys.argv:
        # The Windows double-click launcher opens both browser previews.
        webbrowser.open(f"http://{HOST}:{PORT}/")
        webbrowser.open(f"http://{HOST}:{PORT}/desktop_agent.html")
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()

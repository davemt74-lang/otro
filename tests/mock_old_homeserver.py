from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.security import OWNER_CONTROL_TOKEN
from desktop.single_instance import SingleInstance


HOST = "127.0.0.1"
PORT = 4377


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeServerMock/2.2"

    def _json(self, status: int, payload: dict, *, cookie: str | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v1/health":
            self._json(200, {"ok": True, "version": "2.2"})
            return
        self._json(404, {"detail": "Not found"})

    def do_POST(self):
        if self.path == "/__owner/session":
            if self.headers.get("X-HomeServer-Owner") != OWNER_CONTROL_TOKEN:
                self._json(401, {"detail": "Owner authorization failed"})
                return
            self._json(200, {"ok": True}, cookie="homeserver_owner=mock; Path=/; HttpOnly; SameSite=Strict")
            return

        if self.path == "/api/v1/control/system/shutdown":
            if "homeserver_owner=mock" not in (self.headers.get("Cookie") or ""):
                self._json(401, {"detail": "Owner control authorization required"})
                return
            self._json(200, {"accepted": True, "command": "shutdown"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._json(404, {"detail": "Not found"})

    def log_message(self, *_args):
        pass


def main() -> None:
    data_dir = os.environ.get("HOMESERVER_DATA_DIR")
    if not data_dir:
        raise SystemExit("HOMESERVER_DATA_DIR is required")

    instance = SingleInstance(data_dir)
    if not instance.acquire():
        raise SystemExit("Could not acquire HomeServer single-instance mutex")

    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
        server.serve_forever(poll_interval=0.1)
        server.server_close()
    finally:
        instance.release()


if __name__ == "__main__":
    main()

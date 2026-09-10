from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-file-actions-remote-v039-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app import bridge  # noqa: F401,E402  # installs relay extensions
    from app.services import local_file_actions_remote, remote_bridge  # noqa: E402

    calls: list[dict] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload: dict):
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, path: str, *, json: dict | None = None, headers: dict | None = None, **kwargs):
            calls.append({"path": path, "json": json or {}, "headers": headers or {}})
            return FakeResponse({"accepted": True})

    original_client = local_file_actions_remote.httpx.Client
    local_file_actions_remote.httpx.Client = FakeClient
    token = "r" * 24
    try:
        for operation in ("files.update", "files.delete"):
            try:
                remote_bridge.dispatch_remote_request(operation, {"ref": "hsf-1-0123456789abcdef"}, None)
                raise AssertionError(f"{operation} accepted a missing paired-app token")
            except remote_bridge.RemoteBridgeError:
                pass

        try:
            remote_bridge.dispatch_remote_request(
                "files.delete", {"ref": "../../secret.txt"}, token
            )
            raise AssertionError("files.delete accepted a filesystem path")
        except remote_bridge.RemoteBridgeError:
            pass

        try:
            remote_bridge.dispatch_remote_request(
                "files.update",
                {"ref": "hsf-1-0123456789abcdef", "content": "updated", "path": "C:/secret.txt"},
                token,
            )
            raise AssertionError("files.update accepted an extra path argument")
        except remote_bridge.RemoteBridgeError:
            pass

        updated = remote_bridge.dispatch_remote_request(
            "files.update",
            {"ref": "hsf-7-0123456789abcdef", "content": "RELAY_UPDATE_3907"},
            token,
        )
        assert updated["ok"] is True
        update_call = calls[-1]
        assert update_call["path"] == "/api/v1/tools/files.update/execute"
        assert update_call["headers"] == {"Authorization": f"Bearer {token}"}
        assert update_call["json"] == {
            "arguments": {"ref": "hsf-7-0123456789abcdef", "content": "RELAY_UPDATE_3907"}
        }

        deleted = remote_bridge.dispatch_remote_request(
            "files.delete", {"ref": "hsf-9-fedcba9876543210"}, token
        )
        assert deleted["ok"] is True
        delete_call = calls[-1]
        assert delete_call["path"] == "/api/v1/tools/files.delete/execute"
        assert delete_call["headers"] == {"Authorization": f"Bearer {token}"}
        assert delete_call["json"] == {
            "arguments": {"ref": "hsf-9-fedcba9876543210"}
        }
    finally:
        local_file_actions_remote.httpx.Client = original_client

print("HomeServer governed file action Remote Bridge regression passed")
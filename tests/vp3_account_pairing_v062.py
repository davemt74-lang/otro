from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

remote_api_source = (ROOT_DIR / "app" / "remote_bridge_api.py").read_text(encoding="utf-8")
remote_browser_source = (ROOT_DIR / "ui" / "remote.js").read_text(encoding="utf-8")
assert 'runtime.pop("claim_code", None)' in remote_api_source
assert "claim_code" not in remote_browser_source
assert "bootstrap-vp3" not in remote_browser_source
assert "pairing_ready" not in remote_browser_source

with tempfile.TemporaryDirectory(prefix="homeserver-vp3-pairing-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.services import cloud_pairing

    calls: list[dict] = []
    sessions: list[tuple[str, str]] = []
    settings: list[tuple[str, bool]] = []
    local_device = "hs-0123456789abcdef01234567"
    local_token = "L" * 64
    cloud_session = "S" * 64
    account_token = "VP3-" + "-".join(["A1B2C3D4"] * 8)

    class Response:
        status_code = 200
        def json(self):
            return {
                "ok": True,
                "device_id": local_device,
                "transport": "vp3_https",
                "protocol": "https-relay-v1",
                "session_token": cloud_session,
                "poll_url": "https://vp3.me/api/homeserver-https-poll-v1300.php",
                "poll_after_ms": 900,
            }

    class Client:
        def __init__(self, *args, **kwargs):
            calls.append({"init": kwargs})
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def post(self, path, **kwargs):
            calls.append({"method": "POST", "path": path, **kwargs})
            return Response()

    originals = {
        "client": cloud_pairing.httpx.Client,
        "identity": cloud_pairing.load_or_create_remote_identity,
        "create": cloud_pairing.create_pairing_request,
        "approve": cloud_pairing.approve_pairing_request,
        "dispatch": cloud_pairing.dispatch_remote_request,
        "save_session": cloud_pairing.save_https_session,
        "save_settings": cloud_pairing.save_vp3_https_settings,
        "revoke": cloud_pairing._revoke_local_vp3_pairing,
    }
    cloud_pairing.httpx.Client = Client
    cloud_pairing.load_or_create_remote_identity = lambda: {"device_id": local_device}
    cloud_pairing.create_pairing_request = lambda app_key, app_name, permissions: {
        "request_id": "local-request",
        "claim_token": local_token,
        "permissions": permissions,
    }
    cloud_pairing.approve_pairing_request = lambda request_id: {
        "app_key": "vp3",
        "permissions": list(cloud_pairing._VP3_PERMISSIONS),
        "delivery": "claim_token",
    }
    cloud_pairing.dispatch_remote_request = lambda operation, payload, bearer: {
        "ok": True,
        "status": 200,
        "payload": {"version": "2.3", "features": ["agent.chat"]},
    }
    cloud_pairing.save_https_session = lambda endpoint, token: sessions.append((endpoint, token)) or {"configured": True}
    cloud_pairing.save_vp3_https_settings = lambda endpoint, enabled=True: settings.append((endpoint, enabled)) or {
        "transport": "vp3_https", "enabled": enabled, "https_endpoint": endpoint
    }
    cloud_pairing._revoke_local_vp3_pairing = lambda: None

    try:
        result = cloud_pairing.redeem_vp3_pairing_token(account_token)
        sent = calls[-1]["json"]
        assert sent["pairing_token"] == account_token
        assert sent["device_id"] == local_device
        assert sent["homeserver_token"] == local_token
        assert sent["version"] == "2.3"
        assert "relay_claim" not in sent
        assert calls[-1]["path"] == "https://vp3.me/api/homeserver-https-pair-v1300.php"
        assert sessions[-1] == ("https://vp3.me/api/homeserver-https-poll-v1300.php", cloud_session)
        assert settings[-1] == ("https://vp3.me/api/homeserver-https-poll-v1300.php", True)
        assert result["accepted"] is True
        assert result["device_id"] == local_device
        assert result["transport"] == "vp3_https"

        try:
            cloud_pairing.redeem_vp3_pairing_token("not-a-vp3-token")
            raise AssertionError("Invalid account pairing token was accepted")
        except cloud_pairing.CloudPairingError:
            pass
    finally:
        cloud_pairing.httpx.Client = originals["client"]
        cloud_pairing.load_or_create_remote_identity = originals["identity"]
        cloud_pairing.create_pairing_request = originals["create"]
        cloud_pairing.approve_pairing_request = originals["approve"]
        cloud_pairing.dispatch_remote_request = originals["dispatch"]
        cloud_pairing.save_https_session = originals["save_session"]
        cloud_pairing.save_vp3_https_settings = originals["save_settings"]
        cloud_pairing._revoke_local_vp3_pairing = originals["revoke"]

print("VP3 account-issued HomeServer pairing regression passed")

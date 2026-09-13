from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

with tempfile.TemporaryDirectory(prefix="homeserver-vp3-pairing-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.services import cloud_pairing

    calls: list[dict] = []
    saved_settings: list[tuple[bool, str]] = []
    local_device = "hs-0123456789abcdef01234567"
    account_token = "VP3-" + "-".join(["A1B2C3D4"] * 8)

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *args, **kwargs):
            calls.append({"init": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path, **kwargs):
            calls.append({"method": "GET", "path": path, **kwargs})
            return Response({
                "ok": True,
                "pairing_protocol": "account-token-v1",
                "relay_websocket_url": "wss://relay.example.test/bridge",
            })

        def post(self, path, **kwargs):
            calls.append({"method": "POST", "path": path, **kwargs})
            return Response({
                "ok": True,
                "device_id": local_device,
                "request_id": "synthetic-request",
                "expires_at": "2026-09-13T14:00:00+00:00",
                "permissions": ["agent.chat", "knowledge.search"],
            })

    original_client = cloud_pairing.httpx.Client
    original_status = cloud_pairing.bridge_status
    original_save = cloud_pairing.save_bridge_settings
    cloud_pairing.httpx.Client = Client
    cloud_pairing.save_bridge_settings = lambda enabled, url: (
        saved_settings.append((enabled, url)) or {"enabled": enabled, "broker_url": url}
    )
    cloud_pairing.bridge_status = lambda: {
        "settings": {"enabled": True, "broker_url": "wss://relay.example.test/bridge"},
        "runtime": {"connected": True, "claimed": False, "claim_code": "AB12-CD34-EF56"},
        "identity": {"device_id": local_device},
    }
    try:
        bootstrap = cloud_pairing.bootstrap_vp3_remote_bridge()
        assert bootstrap["configured"] is True
        assert saved_settings[-1] == (True, "wss://relay.example.test/bridge")
        assert calls[-1]["path"] == "https://vp3.me/api/homeserver-relay-bootstrap-v1210.php"

        result = cloud_pairing.redeem_vp3_pairing_token(account_token)
        sent = calls[-1]["json"]
        assert sent["pairing_token"] == account_token
        assert sent["device_id"] == local_device
        assert sent["relay_claim"] == "AB12-CD34-EF56"
        assert result["accepted"] is True
        assert result["device_id"] == local_device
        assert "relay_claim" not in result
        assert calls[-1]["path"] == "https://vp3.me/api/homeserver-pair-v1210.php"

        try:
            cloud_pairing.redeem_vp3_pairing_token("not-a-vp3-token")
            raise AssertionError("Invalid account pairing token was accepted")
        except cloud_pairing.CloudPairingError:
            pass
    finally:
        cloud_pairing.httpx.Client = original_client
        cloud_pairing.bridge_status = original_status
        cloud_pairing.save_bridge_settings = original_save

print("VP3 account-issued HomeServer pairing regression passed")

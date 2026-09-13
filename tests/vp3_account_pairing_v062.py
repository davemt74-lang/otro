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
    local_device = "hs-0123456789abcdef01234567"
    account_token = "VP3-" + "-".join(["A1B2C3D4"] * 8)

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ok": True,
                "device_id": local_device,
                "request_id": "synthetic-request",
                "expires_at": "2026-09-13T14:00:00+00:00",
                "permissions": ["agent.chat", "knowledge.search"],
            }

    class Client:
        def __init__(self, *args, **kwargs):
            calls.append({"init": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, path, **kwargs):
            calls.append({"path": path, **kwargs})
            return Response()

    original_client = cloud_pairing.httpx.Client
    original_status = cloud_pairing.bridge_status
    cloud_pairing.httpx.Client = Client
    cloud_pairing.bridge_status = lambda: {
        "settings": {"enabled": True, "broker_url": "wss://relay.example.test/bridge"},
        "runtime": {"connected": True, "claimed": False, "claim_code": "AB12-CD34-EF56"},
        "identity": {"device_id": local_device},
    }
    try:
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

print("VP3 account-issued HomeServer pairing regression passed")

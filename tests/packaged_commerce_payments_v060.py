from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

BASE = "http://127.0.0.1:4377"


def wait_health(expected_up: bool, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    with httpx.Client(base_url=BASE, timeout=0.8, trust_env=False) as client:
        while time.time() < deadline:
            up = False
            try:
                response = client.get("/api/v1/health")
                up = response.status_code == 200
            except Exception:
                up = False
            if up is expected_up:
                return True
            time.sleep(0.25)
    return False


def authorize() -> httpx.Client:
    client = httpx.Client(base_url=BASE, timeout=4.0, trust_env=False)
    response = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
    assert response.status_code == 200, response.text
    return client


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True), "packaged HomeServer did not become healthy"

    first = authorize()
    try:
        empty = first.get("/api/v1/control/payments")
        assert empty.status_code == 200, empty.text
        assert empty.json()["contract"] == "commerce-payment-v1"
        saved = first.put(
            "/api/v1/control/payments/stripe",
            json={
                "secret_key":"sk_test_packaged_commerce_v060_secret",
                "webhook_secret":"whsec_packaged_commerce_v060_secret",
            },
        )
        assert saved.status_code == 200, saved.text
        serialized = saved.text
        assert "sk_test_packaged_commerce_v060_secret" not in serialized
        assert "whsec_packaged_commerce_v060_secret" not in serialized
        assert saved.json()["providers"]["stripe"]["configured"] is True
        restart = first.post("/api/v1/control/system/restart")
        assert restart.status_code == 200, restart.text
    finally:
        first.close()

    assert wait_health(False, 15), "packaged HomeServer did not stop for restart"
    assert wait_health(True, 30), "packaged HomeServer did not recover after restart"

    second = authorize()
    try:
        restored = second.get("/api/v1/control/payments")
        assert restored.status_code == 200, restored.text
        stripe = restored.json()["providers"]["stripe"]
        assert stripe["configured"] is True
        assert stripe["webhook_configured"] is True
        assert stripe["secret_key_suffix"] == "cret"
        assert stripe["webhook_secret_suffix"] == "cret"
        assert "sk_test_packaged_commerce_v060_secret" not in restored.text
        assert "whsec_packaged_commerce_v060_secret" not in restored.text
        cleared = second.delete("/api/v1/control/payments/stripe")
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["providers"]["stripe"]["configured"] is False
    finally:
        second.close()

    print("Packaged HomeServer v0.60 commerce credential persistence passed")


if __name__ == "__main__":
    main()

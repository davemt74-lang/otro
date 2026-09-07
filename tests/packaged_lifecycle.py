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


def wait_health(expected_up: bool, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    with httpx.Client(base_url=BASE, timeout=0.8, trust_env=False) as client:
        while time.time() < deadline:
            up = False
            try:
                response = client.get("/api/v1/health")
                up = response.status_code == 200 and response.json().get("version") == "0.13.0"
            except Exception:
                up = False
            if up is expected_up:
                return True
            time.sleep(0.2)
    return False


def authorize() -> httpx.Client:
    client = httpx.Client(base_url=BASE, timeout=3.0, trust_env=False)
    response = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
    assert response.status_code == 200
    assert client.get("/api/v1/control/system").status_code == 200
    return client


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True, 20), "packaged HomeServer is not healthy before lifecycle test"

    first = authorize()
    old_cookie = first.cookies.get("homeserver_owner")
    assert old_cookie
    restart = first.post("/api/v1/control/system/restart")
    assert restart.status_code == 200 and restart.json()["accepted"] is True
    first.close()

    # Observe either a brief down transition or enough time for the old process
    # to exit; the definitive proof is a new process-local owner session below.
    wait_health(False, 4)
    assert wait_health(True, 20), "HomeServer did not return after supervised restart"

    old_session = httpx.Client(
        base_url=BASE,
        cookies={"homeserver_owner": old_cookie},
        timeout=3.0,
        trust_env=False,
    )
    try:
        assert old_session.get("/api/v1/control/system").status_code == 401
    finally:
        old_session.close()

    second = authorize()
    shutdown = second.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    second.close()
    assert wait_health(False, 20), "HomeServer listener remained active after supervised shutdown"

    print("Packaged HomeServer restart/session-rotation/shutdown test passed")


if __name__ == "__main__":
    main()

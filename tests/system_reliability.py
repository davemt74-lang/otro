from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-system-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.owner_secret import load_or_create_owner_secret, owner_secret_metadata  # noqa: E402
    from app.services.runtime_control import register_runtime_handler  # noqa: E402

    assert settings.version == "0.18.0"
    assert settings.owner_secret_path.is_file()
    assert load_or_create_owner_secret() == OWNER_CONTROL_TOKEN
    metadata = owner_secret_metadata()
    assert metadata["exists"] is True
    if os.name == "nt":
        assert metadata["protection"] == "windows-dpapi"
        assert OWNER_CONTROL_TOKEN.encode("utf-8") not in settings.owner_secret_path.read_bytes()

    commands: list[str] = []

    with TestClient(app) as owner:
        assert owner.get("/api/v1/health").json()["version"] == "0.18.0"
        assert owner.get("/system").status_code == 401
        assert owner.get("/api/v1/control/system").status_code == 401

        session = owner.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert session.status_code == 200
        assert owner.get("/system").status_code == 200

        summary = owner.get("/api/v1/control/system")
        assert summary.status_code == 200
        payload = summary.json()
        assert payload["setup"]["complete"] is False
        assert payload["diagnostics"]["version"] == "0.18.0"
        assert payload["diagnostics"]["database"]["ok"] is True
        assert payload["diagnostics"]["database"]["schema_version"] == 20
        assert payload["diagnostics"]["owner_security"]["exists"] is True
        assert payload["diagnostics"]["runtime_control"]["available"] is False

        completed = owner.post("/api/v1/control/system/setup", json={"complete": True})
        assert completed.status_code == 200
        assert completed.json()["setup"]["complete"] is True
        assert owner.get("/api/v1/control/system").json()["setup"]["complete"] is True

        startup = owner.put("/api/v1/control/system/startup", json={"enabled": True})
        assert startup.status_code == 422

        register_runtime_handler(lambda command: commands.append(command) is None or True)
        try:
            restarted = owner.post("/api/v1/control/system/restart")
            assert restarted.status_code == 200
            assert restarted.json()["command"] == "restart"
            assert commands == ["restart"]
        finally:
            register_runtime_handler(None)

        pair = owner.post(
            "/api/v1/pairing/request",
            json={"app_key": "system-boundary-test", "app_name": "System Boundary Test", "permissions": ["agent.chat"]},
        )
        assert pair.status_code == 200
        pair_json = pair.json()
        assert owner.post("/api/v1/pairing/approve", json={"code": pair_json["code"]}).status_code == 200
        token = pair_json["claim_token"]

        with TestClient(app) as paired_only:
            assert paired_only.get(
                "/api/v1/control/system",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code == 401
            assert paired_only.get("/system").status_code == 401

print("HomeServer Windows reliability/system test passed")

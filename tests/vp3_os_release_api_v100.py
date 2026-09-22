from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v100-api-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.runtime import app  # noqa: E402
from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
from app.services.tasks import scheduler  # noqa: E402

with TestClient(app) as client:
    scheduler.stop()

    denied = client.get("/api/v1/control/vp3-os/release-readiness")
    assert denied.status_code == 401

    owner = client.post(
        "/__owner/session",
        headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
    )
    assert owner.status_code == 200

    ready = client.get("/api/v1/control/vp3-os/release-readiness")
    assert ready.status_code == 200, ready.text
    payload = ready.json()
    assert payload["release"] == "v1.0"
    assert payload["vp3_os_version"] == "v1.0"
    assert payload["production_ready"] is True
    assert payload["status"] in {"ready", "degraded"}

    public = client.get("/api/v1/capabilities")
    assert public.status_code == 200
    capabilities = public.json()
    assert capabilities["vp3_os"]["os_version"] == "v1.0"
    assert capabilities["vp3_os_release_readiness"]["version"] == "v1.0"
    assert "vp3.os.v100" in capabilities["features"]

tmp.cleanup()
print("VP3 OS v1.0 release readiness API passed")

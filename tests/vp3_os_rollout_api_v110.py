from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v110-api-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.runtime import app  # noqa: E402
from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
from app.services import vp3_os  # noqa: E402
from app.services.tasks import scheduler  # noqa: E402


def package_bytes() -> bytes:
    exe = b"v110-api-exe"
    installer = b"v110-api-installer"
    exe_hash = hashlib.sha256(exe).hexdigest()
    installer_hash = hashlib.sha256(installer).hexdigest()
    manifest = {
        "format": "vp3-os-release-v1",
        "version": vp3_os.VP3_OS_VERSION,
        "channel": "stable",
        "minimum_schema_version": 27,
        "files": {
            "HomeServer.exe": exe_hash,
            "HomeServerSetup.exe": installer_hash,
        },
    }
    sums = (
        f"{exe_hash}  HomeServer.exe\n"
        f"{installer_hash}  HomeServerSetup.exe\n"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("RELEASE.json", json.dumps(manifest))
        archive.writestr("HomeServer.exe", exe)
        archive.writestr("HomeServerSetup.exe", installer)
        archive.writestr("SHA256SUMS.txt", sums)
    return stream.getvalue()


with TestClient(app) as client:
    scheduler.stop()

    assert client.get("/api/v1/control/vp3-os/rollout").status_code == 401
    assert client.post("/api/v1/control/vp3-os/certifications").status_code == 401
    assert client.post("/api/v1/control/vp3-os/support-bundle").status_code == 401

    owner = client.post(
        "/__owner/session",
        headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
    )
    assert owner.status_code == 200

    overview = client.get("/api/v1/control/vp3-os/rollout")
    assert overview.status_code == 200, overview.text
    payload = overview.json()
    assert payload["version"] == "v1.1"
    assert str(payload["vp3_os_version"]).startswith("v1.")
    assert payload["settings"]["release_channel"] == "stable"
    assert payload["settings"]["rollout_ring"] == "pilot"
    assert payload["governance"]["automatic_apply"] is False

    settings = client.put(
        "/api/v1/control/vp3-os/rollout/settings",
        json={
            "release_channel": "stable",
            "rollout_ring": "staged",
            "watchdog_enabled": True,
            "max_failed_starts": 4,
        },
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()["settings"]["rollout_ring"] == "staged"
    assert settings.json()["settings"]["automatic_apply"] is False

    certification = client.post("/api/v1/control/vp3-os/certifications")
    assert certification.status_code == 200, certification.text
    assert certification.json()["result"] == "passed"

    staged = client.post(
        "/api/v1/control/vp3-os/updates/stage",
        files={"package": (f"VP3-OS-{vp3_os.VP3_OS_VERSION}.zip", package_bytes(), "application/zip")},
    )
    assert staged.status_code == 200, staged.text
    staged_payload = staged.json()
    assert staged_payload["status"] == "staged"
    package_id = int(staged_payload["id"])

    approved = client.post(
        f"/api/v1/control/vp3-os/updates/{package_id}/approve"
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["rollback_backup_name"]

    apply = client.post(
        f"/api/v1/control/vp3-os/updates/{package_id}/apply"
    )
    assert apply.status_code == 422

    bundle = client.post("/api/v1/control/vp3-os/support-bundle")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"].startswith("application/zip")
    assert len(bundle.headers.get("x-vp3-sha256", "")) == 64
    with zipfile.ZipFile(io.BytesIO(bundle.content), "r") as archive:
        summary = json.loads(archive.read("support-summary.json"))
        assert summary["privacy"]["credentials_included"] is False
        assert summary["privacy"]["knowledge_content_included"] is False

    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200
    caps = capabilities.json()
    assert str(caps["vp3_os"]["os_version"]).startswith("v1.")
    assert caps["vp3_os_device_rollout"]["version"] == "v1.1"
    assert caps["vp3_os_device_rollout"]["automatic_apply"] is False
    assert "vp3.os.v110" in caps["features"]
    assert "vp3.os.hardware_certification.v1" in caps["features"]
    assert "vp3.os.controlled_rollout.v1" in caps["features"]

tmp.cleanup()
print("VP3 OS v1.1 rollout API passed")

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v110-rollout-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import backups, device_rollout, vp3_os  # noqa: E402
from app.services.owner_secret import load_or_create_owner_secret  # noqa: E402

initialize_database()
load_or_create_owner_secret()

assert vp3_os.VP3_OS_VERSION.startswith("v1.")
settings = device_rollout.get_settings()
assert settings["release_channel"] == "stable"
assert settings["rollout_ring"] == "pilot"
assert settings["automatic_apply"] is False
assert settings["watchdog_enabled"] is True

initial = device_rollout.commissioning_report()
assert initial["state"] == "degraded"
assert initial["commissionable"] is True
assert "no_backup" in initial["warnings"]
assert initial["profile"]["key"] == "custom"

certification = device_rollout.certify_hardware()
assert certification["result"] == "passed"
assert certification["profile"]["key"] == "custom"
assert device_rollout.list_certifications(5)[0]["id"] == certification["id"]

backup = backups.create_backup("v110-commissioning")
assert Path(backup["path"]).is_file()
commissioned = device_rollout.commissioning_report()
assert commissioned["state"] == "ready"
assert commissioned["commissionable"] is True

updated = device_rollout.update_settings(
    release_channel="stable",
    rollout_ring="staged",
    watchdog_enabled=True,
    max_failed_starts=4,
)
assert updated["rollout_ring"] == "staged"
assert updated["automatic_apply"] is False
assert updated["max_failed_starts"] == 4


def release_zip(channel: str = "stable", *, tamper: bool = False) -> bytes:
    exe = b"vp3-home-server-v110"
    installer = b"vp3-home-server-setup-v110"
    exe_hash = hashlib.sha256(exe).hexdigest()
    installer_hash = hashlib.sha256(installer).hexdigest()
    manifest = {
        "format": "vp3-os-release-v1",
        "version": vp3_os.VP3_OS_VERSION,
        "channel": channel,
        "minimum_schema_version": 27,
        "files": {
            "HomeServer.exe": exe_hash,
            "HomeServerSetup.exe": installer_hash,
        },
        "release_notes": "Controlled rollout synthetic package.",
    }
    sums = (
        f"{exe_hash}  HomeServer.exe\n"
        f"{installer_hash}  HomeServerSetup.exe\n"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("RELEASE.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("HomeServer.exe", exe)
        archive.writestr(
            "HomeServerSetup.exe",
            installer + (b"-tampered" if tamper else b""),
        )
        archive.writestr("SHA256SUMS.txt", sums)
    return stream.getvalue()


try:
    device_rollout.stage_package(io.BytesIO(release_zip("beta")), "beta.zip")
except device_rollout.RolloutError as exc:
    assert "does not match configured stable channel" in str(exc)
else:
    raise AssertionError("Mismatched update channel was accepted")


def downgrade_zip() -> bytes:
    exe = b"vp3-home-server-v100"
    installer = b"vp3-home-server-setup-v100"
    exe_hash = hashlib.sha256(exe).hexdigest()
    installer_hash = hashlib.sha256(installer).hexdigest()
    manifest = {
        "format": "vp3-os-release-v1",
        "version": "v1.0",
        "channel": "stable",
        "minimum_schema_version": 26,
        "files": {
            "HomeServer.exe": exe_hash,
            "HomeServerSetup.exe": installer_hash,
        },
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("RELEASE.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("HomeServer.exe", exe)
        archive.writestr("HomeServerSetup.exe", installer)
        archive.writestr(
            "SHA256SUMS.txt",
            f"{exe_hash}  HomeServer.exe\n{installer_hash}  HomeServerSetup.exe\n",
        )
    return stream.getvalue()


try:
    device_rollout.stage_package(io.BytesIO(downgrade_zip()), "downgrade.zip")
except device_rollout.RolloutError as exc:
    assert "older than the installed VP3 OS" in str(exc)
else:
    raise AssertionError("Downgrade package was accepted")

try:
    device_rollout.stage_package(
        io.BytesIO(release_zip("stable", tamper=True)),
        "tampered.zip",
    )
except device_rollout.RolloutError as exc:
    assert "SHA-256 validation" in str(exc)
else:
    raise AssertionError("Tampered installer was accepted")

staged = device_rollout.stage_package(
    io.BytesIO(release_zip()),
    f"VP3-OS-{vp3_os.VP3_OS_VERSION}-test.zip",
)
assert staged["version"] == vp3_os.VP3_OS_VERSION
assert staged["channel"] == "stable"
assert staged["status"] == "staged"
assert len(staged["package_sha256"]) == 64
assert len(staged["installer_sha256"]) == 64
duplicate = device_rollout.stage_package(
    io.BytesIO(release_zip()),
    f"VP3-OS-{vp3_os.VP3_OS_VERSION}-duplicate.zip",
)
assert duplicate["id"] == staged["id"]
assert len(device_rollout.list_packages(20)) == 1

approved = device_rollout.approve_package(staged["id"])
assert approved["status"] == "approved"
assert approved["rollback_backup_name"]
assert any(
    item["name"] == approved["rollback_backup_name"]
    for item in backups.list_backups()
)

if os.name != "nt":
    try:
        device_rollout.request_apply(staged["id"])
    except device_rollout.RolloutError as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("Non-Windows runtime attempted update apply")

with db() as connection:
    connection.execute(
        """
        UPDATE vp3_rollout_packages
        SET status='applying',updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (staged["id"],),
    )
apply_dir = Path(tmp.name) / "runtime" / "updates" / f"apply-{staged['id']}"
apply_dir.mkdir(parents=True, exist_ok=True)
(apply_dir / "update-result.json").write_text(
    json.dumps({"status": "applied", "reason": "health_check_passed"}),
    encoding="utf-8",
)
reconciled = device_rollout.reconcile_update_results()
assert len(reconciled) == 1
assert reconciled[0]["status"] == "applied"

with db() as connection:
    connection.execute(
        """
        INSERT INTO agent_memory(memory_key,content,importance)
        VALUES ('v110-private','SUPER-SECRET-MEMORY-CONTENT',1.0)
        """
    )
    connection.execute(
        """
        INSERT INTO knowledge_items(title,kind,content,content_hash)
        VALUES ('Private support test','note','SUPER-SECRET-KNOWLEDGE-CONTENT','v110-private')
        """
    )

bundle = device_rollout.support_bundle()
bundle_path = Path(bundle["path"])
assert bundle_path.is_file()
assert len(bundle["sha256"]) == 64
with zipfile.ZipFile(bundle_path, "r") as archive:
    assert set(archive.namelist()) == {
        "support-summary.json",
        "diagnostics.json",
        "commissioning.json",
        "rollout-events.json",
    }
    combined = b"\n".join(archive.read(name) for name in archive.namelist())
    assert b"SUPER-SECRET-MEMORY-CONTENT" not in combined
    assert b"SUPER-SECRET-KNOWLEDGE-CONTENT" not in combined
    assert str(Path(tmp.name).resolve()).encode("utf-8") not in combined
    summary = json.loads(archive.read("support-summary.json"))
    assert summary["privacy"]["conversations_included"] is False
    assert summary["privacy"]["recordings_included"] is False
    assert summary["privacy"]["knowledge_content_included"] is False
    assert summary["privacy"]["credentials_included"] is False
    assert summary["privacy"]["absolute_paths_included"] is False

overview = device_rollout.overview()
assert overview["version"] == "v1.1"
assert str(overview["vp3_os_version"]).startswith("v1.")
assert overview["governance"]["automatic_apply"] is False
assert overview["governance"]["remote_unattended_updates"] is False
assert overview["governance"]["pre_update_backup_required"] is True
assert overview["packages"][0]["status"] == "applied"
assert any(item["event_type"] == "hardware.certified" for item in overview["events"])
assert any(item["event_type"] == "update.applied" for item in overview["events"])

tmp.cleanup()
print("VP3 OS v1.1 controlled rollout runtime passed")

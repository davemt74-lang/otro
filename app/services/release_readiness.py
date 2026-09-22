from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db, migration_files
from . import (
    ambient_orchestration,
    automation_intelligence,
    local_automation,
    room_device_automation,
    vp3_os,
)
from .backups import list_backups, pending_restore_info
from .owner_secret import owner_secret_metadata

RELEASE_VERSION = "v1.0"
MIN_SCHEMA_VERSION = 26
MIN_FREE_BYTES = 256 * 1024 * 1024


def _check(name: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        **details,
    }


def _database_check() -> dict[str, Any]:
    supported = max([1, *(version for version, _ in migration_files())])
    try:
        connection = sqlite3.connect(settings.db_path, timeout=3)
        try:
            quick_rows = connection.execute("PRAGMA quick_check").fetchall()
            quick = [str(row[0]) for row in quick_rows]
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 1) FROM schema_migrations"
            ).fetchone()
            schema_version = int(row[0]) if row else 1
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as exc:
        return _check(
            "database",
            "blocked",
            "Database integrity could not be verified.",
            error=type(exc).__name__,
            supported_schema_version=supported,
        )

    ok = quick == ["ok"] and not violations and schema_version >= MIN_SCHEMA_VERSION
    return _check(
        "database",
        "ready" if ok else "blocked",
        "Database integrity and schema are release-ready."
        if ok
        else "Database integrity, foreign keys, or schema are not release-ready.",
        quick_check="ok" if quick == ["ok"] else "; ".join(quick[:5]),
        foreign_key_violations=len(violations),
        schema_version=schema_version,
        supported_schema_version=supported,
        minimum_schema_version=MIN_SCHEMA_VERSION,
    )


def _data_directory_check() -> dict[str, Any]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(settings.data_dir)
    writable = False
    error = ""
    probe_path: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(prefix=".vp3-release-", dir=settings.data_dir)
        os.close(fd)
        probe_path = Path(raw)
        probe_path.write_bytes(b"vp3")
        writable = probe_path.read_bytes() == b"vp3"
    except OSError as exc:
        error = type(exc).__name__
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not writable:
        return _check(
            "data_directory",
            "blocked",
            "HomeServer data directory is not writable.",
            free_bytes=usage.free,
            error=error or None,
        )
    if usage.free < MIN_FREE_BYTES:
        return _check(
            "data_directory",
            "degraded",
            "HomeServer data directory is writable but low on free space.",
            free_bytes=usage.free,
            minimum_free_bytes=MIN_FREE_BYTES,
        )
    return _check(
        "data_directory",
        "ready",
        "HomeServer data directory is writable with release headroom.",
        free_bytes=usage.free,
        minimum_free_bytes=MIN_FREE_BYTES,
    )


def _owner_security_check() -> dict[str, Any]:
    metadata = owner_secret_metadata()
    if not metadata.get("exists"):
        return _check(
            "owner_security",
            "blocked",
            "Owner bootstrap credential is unavailable.",
            protection=metadata.get("protection"),
        )
    if metadata.get("recovered_corrupt_secret"):
        return _check(
            "owner_security",
            "degraded",
            "Owner credential was recovered from a corrupt prior secret.",
            protection=metadata.get("protection"),
        )
    return _check(
        "owner_security",
        "ready",
        "Owner credential is present and protected by the platform policy.",
        protection=metadata.get("protection"),
    )


def _backup_check() -> dict[str, Any]:
    try:
        items = list_backups()
        pending = pending_restore_info() or {}
    except Exception as exc:
        return _check(
            "backup_recovery",
            "degraded",
            "Backup/recovery status could not be fully inspected.",
            error=type(exc).__name__,
        )
    if pending.get("valid"):
        return _check(
            "backup_recovery",
            "degraded",
            "A validated restore is staged for the next startup.",
            backup_count=len(items),
            pending_restore=True,
        )
    if not items:
        return _check(
            "backup_recovery",
            "degraded",
            "No local safety backup exists yet.",
            backup_count=0,
            pending_restore=False,
        )
    return _check(
        "backup_recovery",
        "ready",
        "Backup/recovery subsystem has at least one local safety backup.",
        backup_count=len(items),
        pending_restore=False,
    )


def _hardware_check() -> dict[str, Any]:
    profile = vp3_os.profile_definition()
    inventory = vp3_os.hardware_inventory()
    expected = list(profile.get("expected_hardware") or [])
    unavailable = [key for key in expected if not inventory.get(key, {}).get("ready")]

    privacy = vp3_os.manifest(
        include_hardware=False,
        include_device_id=False,
    )["privacy"]
    if (
        privacy.get("privacy_switch_engaged")
        and not privacy.get("physical_microphone_disconnect_verified")
    ):
        return _check(
            "hardware",
            "blocked",
            "Privacy switch is engaged but the physical microphone disconnect is not verified.",
            profile=profile["key"],
            unavailable_hardware=unavailable,
        )

    if unavailable:
        return _check(
            "hardware",
            "degraded",
            "Expected hardware is not fully ready; software remains available in degraded mode.",
            profile=profile["key"],
            unavailable_hardware=unavailable,
        )

    return _check(
        "hardware",
        "ready",
        "Configured VP3 OS hardware profile is ready.",
        profile=profile["key"],
        unavailable_hardware=[],
    )


def _governance_check() -> dict[str, Any]:
    room = room_device_automation.public_capability()
    local = local_automation.public_capability()
    intelligence = automation_intelligence.public_capability()
    orchestration = ambient_orchestration.public_capability()

    invariants = {
        "v060_governed_device_actions": room.get("governed_device_actions") is True,
        "v060_ambient_direct_execution": room.get("ambient_direct_execution") is True,
        "v070_device_commands_require_owner_approval": (
            local.get("device_commands_still_require_owner_approval") is True
        ),
        "v070_direct_physical_execution": local.get("direct_physical_execution") is True,
        "v080_explicit_owner_enable_required": (
            intelligence.get("explicit_owner_enable_required") is True
        ),
        "v080_direct_physical_execution": intelligence.get("direct_physical_execution") is True,
        "v090_ambient_auto_activation": orchestration.get("ambient_auto_activation") is True,
        "v090_direct_physical_execution": orchestration.get("direct_physical_execution") is True,
        "v090_device_commands_require_v060_owner_approval": (
            orchestration.get("device_commands_require_v060_owner_approval") is True
        ),
    }

    safe = (
        room.get("governed_device_actions") is True
        and room.get("ambient_direct_execution") is False
        and local.get("device_commands_still_require_owner_approval") is True
        and local.get("direct_physical_execution") is False
        and intelligence.get("explicit_owner_enable_required") is True
        and intelligence.get("direct_physical_execution") is False
        and orchestration.get("ambient_auto_activation") is False
        and orchestration.get("direct_physical_execution") is False
        and orchestration.get("device_commands_require_v060_owner_approval") is True
    )
    return _check(
        "physical_action_governance",
        "ready" if safe else "blocked",
        "Physical actions remain behind the v0.60/v0.70 owner-approval boundary."
        if safe
        else "Physical-action governance invariants are not release-safe.",
        invariants=invariants,
    )


def report() -> dict[str, Any]:
    checks = [
        _database_check(),
        _data_directory_check(),
        _owner_security_check(),
        _backup_check(),
        _hardware_check(),
        _governance_check(),
    ]
    statuses = {item["status"] for item in checks}
    overall = "blocked" if "blocked" in statuses else (
        "degraded" if "degraded" in statuses else "ready"
    )
    return {
        "release": RELEASE_VERSION,
        "vp3_os_version": vp3_os.VP3_OS_VERSION,
        "status": overall,
        "production_ready": overall != "blocked",
        "checks": checks,
        "required_release_gates": {
            "fresh_install": True,
            "upgrade_recovery": True,
            "governed_room_automation": True,
            "bounded_soak": True,
            "cross_platform_ci": True,
            "post_merge_validation": True,
        },
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": RELEASE_VERSION,
        "production_release_hardening": True,
        "fresh_install_validation": True,
        "upgrade_recovery_validation": True,
        "governed_end_to_end_validation": True,
        "bounded_soak_validation": True,
        "direct_physical_execution": False,
    }

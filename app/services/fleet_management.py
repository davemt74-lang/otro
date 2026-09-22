from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from . import device_rollout, system_state, vp3_os
from .remote_identity import remote_identity_metadata

FLEET_VERSION = "v1.2"
DEVICE_ID_RE = re.compile(r"^hs-[0-9a-f]{24}$")
APP_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
REQUEST_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^v\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9._-]+)?$")
CHANNELS = {"stable", "beta", "dev"}
RINGS = {"pilot", "staged", "broad"}
OUTCOMES = {
    "pending",
    "staged",
    "approved",
    "applying",
    "healthy",
    "failed",
    "rolled_back",
    "offline",
    "degraded",
}
_BAD_OUTCOMES = {"failed", "rolled_back"}
_ACTIVE_PACKAGE_STATES = {"staged", "approved", "applying", "failed", "rolled_back"}


class FleetError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _read_json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event(
    event_type: str,
    summary: str,
    *,
    severity: str = "info",
    device_id: str | None = None,
    rollout_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_metadata = {}
    allowed = {
        "release_version",
        "channel",
        "rollout_ring",
        "outcome",
        "detail_code",
        "request_key",
        "package_sha256",
        "controller_app_key",
        "reason",
        "failure_count",
        "failure_threshold",
    }
    for key, value in (metadata or {}).items():
        if key in allowed:
            safe_metadata[key] = value
    try:
        with db() as connection:
            connection.execute(
                """
                INSERT INTO vp3_fleet_events(
                    event_type,severity,device_id,rollout_id,summary,metadata_json
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    str(event_type)[:100],
                    severity if severity in {"info", "warning", "error"} else "info",
                    str(device_id)[:80] if device_id else None,
                    int(rollout_id) if rollout_id else None,
                    str(summary)[:240],
                    _json(safe_metadata),
                ),
            )
    except Exception:
        pass


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT enabled,controller_app_key,device_label,
                   remote_diagnostics,remote_update_requests,remote_support_summary,
                   telemetry_interval_seconds,stale_after_seconds,
                   rollout_failure_threshold,updated_at
            FROM vp3_fleet_settings WHERE id=1
            """
        ).fetchone()
    if row is None:
        raise FleetError("Fleet settings are unavailable.", 503)
    return {
        "enabled": bool(row["enabled"]),
        "controller_app_key": row["controller_app_key"] or None,
        "device_label": str(row["device_label"] or ""),
        "remote_diagnostics": bool(row["remote_diagnostics"]),
        "remote_update_requests": bool(row["remote_update_requests"]),
        "remote_support_summary": bool(row["remote_support_summary"]),
        "telemetry_interval_seconds": int(row["telemetry_interval_seconds"]),
        "stale_after_seconds": int(row["stale_after_seconds"]),
        "rollout_failure_threshold": int(row["rollout_failure_threshold"]),
        "updated_at": row["updated_at"],
    }


def update_settings(
    *,
    enabled: bool | None = None,
    controller_app_key: str | None = None,
    device_label: str | None = None,
    remote_diagnostics: bool | None = None,
    remote_update_requests: bool | None = None,
    remote_support_summary: bool | None = None,
    telemetry_interval_seconds: int | None = None,
    stale_after_seconds: int | None = None,
    rollout_failure_threshold: int | None = None,
) -> dict[str, Any]:
    current = get_settings()
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    if controller_app_key is None:
        controller = current["controller_app_key"]
    else:
        controller = str(controller_app_key or "").strip() or None
    if controller is not None and not APP_KEY_RE.fullmatch(controller):
        raise FleetError("Controller app key is invalid.")
    label = (
        current["device_label"]
        if device_label is None
        else str(device_label).strip()[:120]
    )
    telemetry = (
        current["telemetry_interval_seconds"]
        if telemetry_interval_seconds is None
        else int(telemetry_interval_seconds)
    )
    stale = (
        current["stale_after_seconds"]
        if stale_after_seconds is None
        else int(stale_after_seconds)
    )
    threshold = (
        current["rollout_failure_threshold"]
        if rollout_failure_threshold is None
        else int(rollout_failure_threshold)
    )
    if telemetry < 60 or telemetry > 86400:
        raise FleetError("Telemetry interval must be between 60 and 86400 seconds.")
    if stale < 120 or stale > 604800:
        raise FleetError("Stale-device threshold must be between 120 seconds and 7 days.")
    if threshold < 1 or threshold > 100:
        raise FleetError("Rollout failure threshold must be between 1 and 100.")
    if next_enabled and not controller:
        raise FleetError("Fleet enrollment requires a controller app key.")
    if next_enabled:
        with db() as connection:
            app = connection.execute(
                """
                SELECT id FROM paired_apps
                WHERE app_key=? AND status='active'
                LIMIT 1
                """,
                (controller,),
            ).fetchone()
            if app is None:
                raise FleetError(
                    "Fleet controller must be an active paired application.",
                    409,
                )
            permissions = {
                row["permission"]
                for row in connection.execute(
                    """
                    SELECT permission FROM app_permissions
                    WHERE paired_app_id=? AND allowed=1
                    """,
                    (int(app["id"]),),
                ).fetchall()
            }
        required_permissions = {"fleet.read", "fleet.manage", "fleet.telemetry"}
        missing_permissions = sorted(required_permissions - permissions)
        if missing_permissions:
            raise FleetError(
                "Fleet controller is missing permissions: "
                + ", ".join(missing_permissions),
                409,
            )

    diagnostic = (
        current["remote_diagnostics"]
        if remote_diagnostics is None
        else bool(remote_diagnostics)
    )
    update_requests = (
        current["remote_update_requests"]
        if remote_update_requests is None
        else bool(remote_update_requests)
    )
    support = (
        current["remote_support_summary"]
        if remote_support_summary is None
        else bool(remote_support_summary)
    )
    if not next_enabled:
        diagnostic = False
        update_requests = False
        support = False

    with db() as connection:
        connection.execute(
            """
            UPDATE vp3_fleet_settings
            SET enabled=?,controller_app_key=?,device_label=?,
                remote_diagnostics=?,remote_update_requests=?,remote_support_summary=?,
                telemetry_interval_seconds=?,stale_after_seconds=?,
                rollout_failure_threshold=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                int(next_enabled),
                controller,
                label,
                int(diagnostic),
                int(update_requests),
                int(support),
                telemetry,
                stale,
                threshold,
            ),
        )
    _event(
        "fleet.settings_updated",
        "VP3 fleet settings updated.",
        metadata={"controller_app_key": controller or ""},
    )
    return get_settings()


def decommission_local() -> dict[str, Any]:
    current = get_settings()
    with db() as connection:
        controller = current.get("controller_app_key")
        if controller:
            app = connection.execute(
                "SELECT id FROM paired_apps WHERE app_key=? LIMIT 1",
                (controller,),
            ).fetchone()
            if app is not None:
                connection.execute(
                    """
                    UPDATE app_permissions
                    SET allowed=0,updated_at=CURRENT_TIMESTAMP
                    WHERE paired_app_id=?
                      AND permission IN ('fleet.read','fleet.manage','fleet.telemetry')
                    """,
                    (int(app["id"]),),
                )
        connection.execute(
            """
            UPDATE vp3_fleet_settings
            SET enabled=0,controller_app_key=NULL,
                remote_diagnostics=0,remote_update_requests=0,
                remote_support_summary=0,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """
        )
    _event(
        "fleet.decommissioned",
        "Local VP3 fleet enrollment disabled without deleting private HomeServer data.",
        severity="warning",
        metadata={"controller_app_key": current.get("controller_app_key") or ""},
    )
    return get_settings()


def controller_authorized(identity: dict[str, Any], permission: str) -> bool:
    fleet = get_settings()
    return bool(
        fleet["enabled"]
        and fleet["controller_app_key"]
        and str(identity.get("app_key") or "") == fleet["controller_app_key"]
        and permission in set(identity.get("permissions") or [])
    )


def _watchdog_failure_count() -> int:
    path = settings.runtime_dir / "watchdog-state.json"
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, min(int(payload.get("count") or 0), 1000))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _storage_state(free_bytes: int) -> str:
    if free_bytes < 256 * 1024 * 1024:
        return "critical"
    if free_bytes < 1024 * 1024 * 1024:
        return "low"
    return "ok"


def _backup_state(diagnostics: dict[str, Any]) -> str:
    backups = diagnostics.get("backups") if isinstance(diagnostics.get("backups"), dict) else {}
    count = int(backups.get("count") or 0)
    if count < 1:
        return "missing"
    latest = backups.get("latest") if isinstance(backups.get("latest"), dict) else {}
    created = _parse_time(latest.get("created_at"))
    if created is None:
        return "unknown"
    return "stale" if _utc_now() - created > timedelta(days=7) else "ready"


def _update_status() -> str:
    try:
        packages = device_rollout.list_packages(20)
    except Exception:
        return "unknown"
    for package in packages:
        status = str(package.get("status") or "")
        if status in _ACTIVE_PACKAGE_STATES:
            return status
    if packages and str(packages[0].get("status") or "") == "applied":
        return "applied"
    return "idle"


def local_device_snapshot() -> dict[str, Any]:
    identity = remote_identity_metadata()
    commissioning = device_rollout.commissioning_report()
    rollout = device_rollout.get_settings()
    certifications = device_rollout.list_certifications(1)
    diagnostics = system_state.diagnostics()
    data = diagnostics.get("data") if isinstance(diagnostics.get("data"), dict) else {}
    hardware = commissioning.get("hardware") if isinstance(commissioning.get("hardware"), dict) else {}
    free_bytes = max(0, int(data.get("free_bytes") or 0))
    fleet = get_settings()
    certification = certifications[0] if certifications else None
    return {
        "format": "vp3-fleet-device-v1",
        "device_id": identity["device_id"],
        "label": fleet["device_label"],
        "profile_key": str((commissioning.get("profile") or {}).get("key") or "custom")[:80],
        "os_version": vp3_os.VP3_OS_VERSION,
        "release_channel": rollout["release_channel"],
        "rollout_ring": rollout["rollout_ring"],
        "commissioning_state": str(commissioning.get("state") or "blocked"),
        "certification_result": (
            str(certification.get("result"))
            if certification and certification.get("result") in {"passed", "degraded", "failed"}
            else None
        ),
        "privacy_fault": bool(hardware.get("privacy_fault")),
        "update_status": _update_status(),
        "backup_state": _backup_state(diagnostics),
        "storage_state": _storage_state(free_bytes),
        "watchdog_failures": _watchdog_failure_count(),
        "reported_at": _iso_now(),
        "privacy": {
            "conversations_included": False,
            "recordings_included": False,
            "memory_content_included": False,
            "knowledge_content_included": False,
            "credentials_included": False,
            "filesystem_paths_included": False,
            "network_addresses_included": False,
        },
    }


def remote_diagnostics_summary() -> dict[str, Any]:
    snapshot = local_device_snapshot()
    snapshot["policy"] = {
        "fleet_enabled": get_settings()["enabled"],
        "remote_diagnostics": get_settings()["remote_diagnostics"],
        "remote_update_requests": get_settings()["remote_update_requests"],
        "remote_support_summary": get_settings()["remote_support_summary"],
        "automatic_update_apply": False,
        "physical_action_authority": False,
    }
    return snapshot


def remote_support_summary() -> dict[str, Any]:
    snapshot = remote_diagnostics_summary()
    return {
        "format": "vp3-fleet-support-summary-v1",
        "device": snapshot,
        "support_bundle_upload": False,
        "owner_support_bundle_available_locally": True,
    }


def _validate_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "").strip()
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise FleetError("Fleet check-in device ID is invalid.")
    channel = str(payload.get("release_channel") or "").strip().lower()
    ring = str(payload.get("rollout_ring") or "").strip().lower()
    state = str(payload.get("commissioning_state") or "").strip().lower()
    cert = payload.get("certification_result")
    cert_value = str(cert).strip().lower() if cert is not None else None
    if channel not in CHANNELS or ring not in RINGS:
        raise FleetError("Fleet check-in release channel or rollout ring is invalid.")
    if state not in {"ready", "degraded", "blocked"}:
        raise FleetError("Fleet check-in commissioning state is invalid.")
    if cert_value not in {None, "passed", "degraded", "failed"}:
        raise FleetError("Fleet check-in certification result is invalid.")
    backup = str(payload.get("backup_state") or "unknown").strip().lower()
    storage = str(payload.get("storage_state") or "unknown").strip().lower()
    if backup not in {"ready", "stale", "missing", "unknown"}:
        raise FleetError("Fleet check-in backup state is invalid.")
    if storage not in {"ok", "low", "critical", "unknown"}:
        raise FleetError("Fleet check-in storage state is invalid.")
    os_version = str(payload.get("os_version") or "").strip()
    if not VERSION_RE.fullmatch(os_version):
        raise FleetError("Fleet check-in OS version is invalid.")
    reported = _parse_time(payload.get("reported_at")) or _utc_now()
    if abs((_utc_now() - reported).total_seconds()) > 86400:
        raise FleetError("Fleet check-in timestamp is outside the accepted window.")
    return {
        "device_id": device_id,
        "label": str(payload.get("label") or "").strip()[:120],
        "profile_key": str(payload.get("profile_key") or "custom").strip()[:80] or "custom",
        "os_version": os_version,
        "release_channel": channel,
        "rollout_ring": ring,
        "commissioning_state": state,
        "certification_result": cert_value,
        "privacy_fault": bool(payload.get("privacy_fault")),
        "update_status": str(payload.get("update_status") or "idle").strip()[:40] or "idle",
        "backup_state": backup,
        "storage_state": storage,
        "watchdog_failures": max(0, min(int(payload.get("watchdog_failures") or 0), 1000)),
        "last_seen_at": reported.isoformat(),
    }


def record_checkin(payload: dict[str, Any]) -> dict[str, Any]:
    item = _validate_snapshot(payload)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO vp3_fleet_inventory(
                device_id,label,profile_key,os_version,release_channel,rollout_ring,
                commissioning_state,certification_result,privacy_fault,update_status,
                backup_state,storage_state,watchdog_failures,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET
                label=excluded.label,
                profile_key=excluded.profile_key,
                os_version=excluded.os_version,
                release_channel=excluded.release_channel,
                rollout_ring=excluded.rollout_ring,
                commissioning_state=excluded.commissioning_state,
                certification_result=excluded.certification_result,
                privacy_fault=excluded.privacy_fault,
                update_status=excluded.update_status,
                backup_state=excluded.backup_state,
                storage_state=excluded.storage_state,
                watchdog_failures=excluded.watchdog_failures,
                last_seen_at=excluded.last_seen_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                item["device_id"], item["label"], item["profile_key"], item["os_version"],
                item["release_channel"], item["rollout_ring"], item["commissioning_state"],
                item["certification_result"], int(item["privacy_fault"]),
                item["update_status"], item["backup_state"], item["storage_state"],
                item["watchdog_failures"], item["last_seen_at"],
            ),
        )
    _event(
        "fleet.checkin",
        "Fleet device check-in recorded.",
        device_id=item["device_id"],
    )
    return get_inventory_device(item["device_id"])


def _inventory_health(item: dict[str, Any], stale_after: int) -> tuple[str, list[str]]:
    issues: list[str] = []
    seen = _parse_time(item.get("last_seen_at"))
    if seen is None or _utc_now() - seen > timedelta(seconds=stale_after):
        issues.append("offline")
    if item.get("privacy_fault"):
        issues.append("privacy_fault")
    if item.get("commissioning_state") == "blocked":
        issues.append("commissioning_blocked")
    elif item.get("commissioning_state") == "degraded":
        issues.append("commissioning_degraded")
    if item.get("certification_result") == "failed":
        issues.append("certification_failed")
    elif item.get("certification_result") == "degraded":
        issues.append("certification_degraded")
    if item.get("backup_state") in {"missing", "stale"}:
        issues.append("backup_" + item["backup_state"])
    if item.get("storage_state") in {"low", "critical"}:
        issues.append("storage_" + item["storage_state"])
    if item.get("update_status") in {"failed", "rolled_back"}:
        issues.append("update_" + item["update_status"])
    if int(item.get("watchdog_failures") or 0) > 0:
        issues.append("watchdog_recovery")
    health = "critical" if any(
        code in issues
        for code in {
            "offline",
            "privacy_fault",
            "commissioning_blocked",
            "certification_failed",
            "storage_critical",
            "update_failed",
        }
    ) else ("warning" if issues else "healthy")
    return health, issues


def get_inventory_device(device_id: str) -> dict[str, Any]:
    if not DEVICE_ID_RE.fullmatch(str(device_id or "")):
        raise FleetError("Fleet device was not found.", 404)
    with db() as connection:
        row = connection.execute(
            """
            SELECT device_id,label,profile_key,os_version,release_channel,rollout_ring,
                   commissioning_state,certification_result,privacy_fault,update_status,
                   backup_state,storage_state,watchdog_failures,last_seen_at,enrolled_at,updated_at
            FROM vp3_fleet_inventory WHERE device_id=?
            """,
            (device_id,),
        ).fetchone()
    if row is None:
        raise FleetError("Fleet device was not found.", 404)
    item = dict(row)
    item["privacy_fault"] = bool(item["privacy_fault"])
    health, issues = _inventory_health(item, get_settings()["stale_after_seconds"])
    item["health"] = health
    item["issues"] = issues
    item["online"] = "offline" not in issues
    return item


def list_inventory() -> list[dict[str, Any]]:
    with db() as connection:
        ids = [
            row["device_id"]
            for row in connection.execute(
                "SELECT device_id FROM vp3_fleet_inventory ORDER BY last_seen_at DESC"
            ).fetchall()
        ]
    return [get_inventory_device(device_id) for device_id in ids]


def remove_inventory_device(device_id: str) -> dict[str, Any]:
    with db() as connection:
        cursor = connection.execute(
            "DELETE FROM vp3_fleet_inventory WHERE device_id=?",
            (str(device_id),),
        )
        if cursor.rowcount != 1:
            raise FleetError("Fleet device was not found.", 404)
    _event(
        "fleet.device_removed",
        "Fleet registry device removed; remote device private data was not touched.",
        device_id=str(device_id),
        severity="warning",
    )
    return {"removed": True, "device_id": str(device_id), "private_data_deleted": False}


def create_rollout(
    release_version: str,
    channel: str,
    rollout_ring: str,
    failure_threshold: int | None = None,
) -> dict[str, Any]:
    version = str(release_version or "").strip()
    channel = str(channel or "").strip().lower()
    ring = str(rollout_ring or "").strip().lower()
    if not VERSION_RE.fullmatch(version):
        raise FleetError("Fleet rollout version is invalid.")
    if channel not in CHANNELS or ring not in RINGS:
        raise FleetError("Fleet rollout channel or ring is invalid.")
    threshold = (
        get_settings()["rollout_failure_threshold"]
        if failure_threshold is None
        else int(failure_threshold)
    )
    if threshold < 1 or threshold > 100:
        raise FleetError("Fleet rollout failure threshold must be between 1 and 100.")
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vp3_fleet_rollouts(
                release_version,channel,rollout_ring,failure_threshold
            ) VALUES (?,?,?,?)
            """,
            (version, channel, ring, threshold),
        )
        rollout_id = int(cursor.lastrowid)
    _event(
        "fleet.rollout_created",
        "Fleet rollout created.",
        rollout_id=rollout_id,
        metadata={
            "release_version": version,
            "channel": channel,
            "rollout_ring": ring,
            "failure_threshold": threshold,
        },
    )
    return get_rollout(rollout_id)


def _rollout_counts(rollout_id: int) -> dict[str, int]:
    counts = {key: 0 for key in sorted(OUTCOMES)}
    with db() as connection:
        rows = connection.execute(
            """
            SELECT outcome,COUNT(*) AS count
            FROM vp3_fleet_rollout_outcomes
            WHERE rollout_id=?
            GROUP BY outcome
            """,
            (int(rollout_id),),
        ).fetchall()
    for row in rows:
        if row["outcome"] in counts:
            counts[row["outcome"]] = int(row["count"])
    counts["total"] = sum(value for key, value in counts.items() if key != "total")
    counts["failures"] = counts["failed"] + counts["rolled_back"]
    return counts


def _eligible_device_count(channel: str, rollout_ring: str) -> int:
    ring_order = {"pilot": 0, "staged": 1, "broad": 2}
    ceiling = ring_order[rollout_ring]
    with db() as connection:
        rows = connection.execute(
            """
            SELECT rollout_ring FROM vp3_fleet_inventory
            WHERE release_channel=?
            """,
            (channel,),
        ).fetchall()
    return sum(
        1
        for row in rows
        if ring_order.get(str(row["rollout_ring"]), 99) <= ceiling
    )


def get_rollout(rollout_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id,release_version,channel,rollout_ring,status,failure_threshold,
                   pause_reason,created_at,started_at,completed_at,updated_at
            FROM vp3_fleet_rollouts WHERE id=?
            """,
            (int(rollout_id),),
        ).fetchone()
    if row is None:
        raise FleetError("Fleet rollout was not found.", 404)
    item = dict(row)
    item["id"] = int(item["id"])
    item["failure_threshold"] = int(item["failure_threshold"])
    item["counts"] = _rollout_counts(int(rollout_id))
    item["eligible_devices"] = _eligible_device_count(
        item["channel"],
        item["rollout_ring"],
    )
    return item


def list_rollouts(limit: int = 50) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM vp3_fleet_rollouts ORDER BY id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        ]
    return [get_rollout(item_id) for item_id in ids]


def set_rollout_status(rollout_id: int, status: str, reason: str = "") -> dict[str, Any]:
    desired = str(status or "").strip().lower()
    if desired not in {"active", "paused", "completed", "cancelled"}:
        raise FleetError("Fleet rollout status transition is invalid.")
    rollout = get_rollout(rollout_id)
    current = rollout["status"]
    allowed = {
        "planned": {"active", "cancelled"},
        "active": {"paused", "completed", "cancelled"},
        "paused": {"active", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    if desired not in allowed.get(current, set()):
        raise FleetError(
            f"Fleet rollout cannot transition from {current} to {desired}.",
            409,
        )
    with db() as connection:
        connection.execute(
            """
            UPDATE vp3_fleet_rollouts
            SET status=?,
                pause_reason=CASE WHEN ?='paused' THEN ? ELSE NULL END,
                started_at=CASE
                    WHEN ?='active' AND started_at IS NULL THEN CURRENT_TIMESTAMP
                    ELSE started_at END,
                completed_at=CASE
                    WHEN ? IN ('completed','cancelled') THEN CURRENT_TIMESTAMP
                    ELSE completed_at END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                desired,
                desired,
                str(reason or "")[:240],
                desired,
                desired,
                int(rollout_id),
            ),
        )
    _event(
        "fleet.rollout_status",
        f"Fleet rollout changed from {current} to {desired}.",
        rollout_id=int(rollout_id),
        severity="warning" if desired in {"paused", "cancelled"} else "info",
        metadata={"reason": str(reason or "")[:120]},
    )
    return get_rollout(rollout_id)


def record_rollout_outcome(
    rollout_id: int,
    device_id: str,
    outcome: str,
    detail_code: str = "",
) -> dict[str, Any]:
    rollout = get_rollout(rollout_id)
    if rollout["status"] not in {"active", "paused"}:
        raise FleetError("Fleet rollout is not accepting device outcomes.", 409)
    device = get_inventory_device(device_id)
    value = str(outcome or "").strip().lower()
    if value not in OUTCOMES:
        raise FleetError("Fleet rollout outcome is invalid.")
    detail = re.sub(r"[^A-Za-z0-9._:-]", "", str(detail_code or ""))[:80]
    with db() as connection:
        connection.execute(
            """
            INSERT INTO vp3_fleet_rollout_outcomes(
                rollout_id,device_id,outcome,detail_code
            ) VALUES (?,?,?,?)
            ON CONFLICT(rollout_id,device_id) DO UPDATE SET
                outcome=excluded.outcome,
                detail_code=excluded.detail_code,
                reported_at=CURRENT_TIMESTAMP
            """,
            (int(rollout_id), device["device_id"], value, detail),
        )
    counts = _rollout_counts(int(rollout_id))
    if (
        rollout["status"] == "active"
        and counts["failures"] >= int(rollout["failure_threshold"])
    ):
        with db() as connection:
            connection.execute(
                """
                UPDATE vp3_fleet_rollouts
                SET status='paused',pause_reason='failure_threshold',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='active'
                """,
                (int(rollout_id),),
            )
        _event(
            "fleet.rollout_auto_paused",
            "Fleet rollout automatically paused after reaching the failure threshold.",
            rollout_id=int(rollout_id),
            severity="error",
            metadata={
                "failure_count": counts["failures"],
                "failure_threshold": int(rollout["failure_threshold"]),
            },
        )
    _event(
        "fleet.rollout_outcome",
        "Fleet rollout device outcome recorded.",
        rollout_id=int(rollout_id),
        device_id=device["device_id"],
        severity="error" if value in _BAD_OUTCOMES else (
            "warning" if value in {"offline", "degraded"} else "info"
        ),
        metadata={"outcome": value, "detail_code": detail},
    )
    return get_rollout(rollout_id)


def request_update(
    requester_app_key: str,
    request_key: str,
    package_sha256: str,
    release_version: str,
    rollout_id: int | None = None,
) -> dict[str, Any]:
    fleet = get_settings()
    if not fleet["enabled"] or not fleet["remote_update_requests"]:
        raise FleetError("Remote fleet update requests are disabled.", 403)
    requester = str(requester_app_key or "").strip()
    if requester != fleet["controller_app_key"]:
        raise FleetError("This app is not the enrolled fleet controller.", 403)
    request_key = str(request_key or "").strip()
    sha = str(package_sha256 or "").strip().lower()
    version = str(release_version or "").strip()
    if not REQUEST_KEY_RE.fullmatch(request_key):
        raise FleetError("Fleet update request key is invalid.")
    if not SHA256_RE.fullmatch(sha):
        raise FleetError("Fleet update package SHA-256 is invalid.")
    if not VERSION_RE.fullmatch(version):
        raise FleetError("Fleet update release version is invalid.")
    if rollout_id is not None:
        get_rollout(int(rollout_id))

    with db() as connection:
        existing = connection.execute(
            """
            SELECT id FROM vp3_fleet_update_requests
            WHERE request_key=?
            """,
            (request_key,),
        ).fetchone()
    if existing is not None:
        return get_update_request(int(existing["id"]))

    packages = device_rollout.list_packages(100)
    matching = next(
        (
            item for item in packages
            if str(item.get("package_sha256") or "").lower() == sha
            and str(item.get("version") or "") == version
            and str(item.get("status") or "") in {"staged", "approved"}
        ),
        None,
    )
    status = "pending_owner" if matching is not None else "unavailable"
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vp3_fleet_update_requests(
                request_key,requester_app_key,package_sha256,
                release_version,status,rollout_id
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                request_key,
                requester,
                sha,
                version,
                status,
                int(rollout_id) if rollout_id is not None else None,
            ),
        )
        request_id = int(cursor.lastrowid)
    _event(
        "fleet.update_requested",
        "Fleet controller requested a locally staged release.",
        rollout_id=int(rollout_id) if rollout_id is not None else None,
        severity="warning" if status == "unavailable" else "info",
        metadata={
            "request_key": request_key,
            "package_sha256": sha,
            "release_version": version,
        },
    )
    return get_update_request(request_id)


def get_update_request(request_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id,request_key,requester_app_key,package_sha256,
                   release_version,status,rollout_id,created_at,decided_at
            FROM vp3_fleet_update_requests WHERE id=?
            """,
            (int(request_id),),
        ).fetchone()
    if row is None:
        raise FleetError("Fleet update request was not found.", 404)
    item = dict(row)
    item["id"] = int(item["id"])
    item["rollout_id"] = int(item["rollout_id"]) if item["rollout_id"] is not None else None
    return item


def list_update_requests(limit: int = 50) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM vp3_fleet_update_requests ORDER BY id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        ]
    return [get_update_request(item_id) for item_id in ids]


def approve_update_request(request_id: int) -> dict[str, Any]:
    request = get_update_request(request_id)
    if request["status"] != "pending_owner":
        raise FleetError("Fleet update request is not awaiting owner approval.", 409)
    packages = device_rollout.list_packages(100)
    package = next(
        (
            item for item in packages
            if str(item.get("package_sha256") or "").lower() == request["package_sha256"]
            and str(item.get("version") or "") == request["release_version"]
            and str(item.get("status") or "") in {"staged", "approved"}
        ),
        None,
    )
    if package is None:
        with db() as connection:
            connection.execute(
                """
                UPDATE vp3_fleet_update_requests
                SET status='unavailable',decided_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending_owner'
                """,
                (int(request_id),),
            )
        raise FleetError("The requested release is no longer staged locally.", 409)
    if package["status"] == "staged":
        package = device_rollout.approve_package(int(package["id"]))
    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE vp3_fleet_update_requests
            SET status='approved',decided_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='pending_owner'
            """,
            (int(request_id),),
        )
        if cursor.rowcount != 1:
            raise FleetError("Fleet update request state changed; refresh and try again.", 409)
    _event(
        "fleet.update_owner_approved",
        "Owner approved the fleet update request; applying the update remains explicit.",
        rollout_id=request["rollout_id"],
        metadata={
            "request_key": request["request_key"],
            "package_sha256": request["package_sha256"],
            "release_version": request["release_version"],
        },
    )
    return {
        "request": get_update_request(request_id),
        "package": package,
        "apply_automatic": False,
    }


def dismiss_update_request(request_id: int) -> dict[str, Any]:
    request = get_update_request(request_id)
    if request["status"] != "pending_owner":
        raise FleetError("Fleet update request is not awaiting owner review.", 409)
    with db() as connection:
        connection.execute(
            """
            UPDATE vp3_fleet_update_requests
            SET status='dismissed',decided_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='pending_owner'
            """,
            (int(request_id),),
        )
    _event(
        "fleet.update_dismissed",
        "Owner dismissed a fleet update request.",
        rollout_id=request["rollout_id"],
        severity="warning",
        metadata={"request_key": request["request_key"]},
    )
    return get_update_request(request_id)


def list_events(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 200))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,event_type,severity,device_id,rollout_id,
                   summary,metadata_json,created_at
            FROM vp3_fleet_events ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["id"] = int(item["id"])
        item["rollout_id"] = int(item["rollout_id"]) if item["rollout_id"] is not None else None
        item["metadata"] = _read_json(item.pop("metadata_json"))
        items.append(item)
    return items


def fleet_alerts() -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for device in list_inventory():
        for issue in device["issues"]:
            alerts.append(
                {
                    "device_id": device["device_id"],
                    "label": device["label"],
                    "issue": issue,
                    "severity": "error" if device["health"] == "critical" else "warning",
                }
            )
    for rollout in list_rollouts(25):
        if rollout["status"] == "paused":
            alerts.append(
                {
                    "rollout_id": rollout["id"],
                    "issue": "rollout_paused",
                    "severity": "error"
                    if rollout.get("pause_reason") == "failure_threshold"
                    else "warning",
                }
            )
    return alerts[:200]


def overview() -> dict[str, Any]:
    return {
        "version": FLEET_VERSION,
        "vp3_os_version": vp3_os.VP3_OS_VERSION,
        "settings": get_settings(),
        "local_device": local_device_snapshot(),
        "inventory": list_inventory(),
        "rollouts": list_rollouts(50),
        "update_requests": list_update_requests(50),
        "alerts": fleet_alerts(),
        "events": list_events(100),
        "governance": {
            "local_device_authority": True,
            "remote_physical_action_authority": False,
            "remote_update_download": False,
            "remote_update_approval": False,
            "remote_update_apply": False,
            "remote_private_data_access": False,
            "owner_staged_release_required": True,
            "owner_update_approval_required": True,
            "v11_updater_reused": True,
            "rollout_auto_pause_on_failures": True,
        },
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": FLEET_VERSION,
        "device_enrollment": True,
        "bounded_health_telemetry": True,
        "hardware_certification_visibility": True,
        "rollout_rings": sorted(RINGS),
        "release_channels": sorted(CHANNELS),
        "rollout_auto_pause": True,
        "remote_diagnostics_policy_gated": True,
        "remote_update_requests_policy_gated": True,
        "remote_update_apply": False,
        "remote_physical_action_authority": False,
        "private_content_telemetry": False,
    }

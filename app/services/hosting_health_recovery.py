from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import hosting_deployment, hosting_diagnostics, hosting_recovery, hosting_runtime, hosting_serving

CONTRACT = "vp3.hosting.health-recovery.v1"
DEFAULT_POLICY = {
    "enabled": True,
    "interval_seconds": 60,
    "failure_threshold": 3,
    "max_recovery_attempts": 2,
    "cooldown_seconds": 300,
    "auto_reactivate": True,
    "auto_rollback": True,
    "auto_restore": False,
}
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.RLock()


class HealthRecoveryError(hosting_runtime.HostingError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(site_id: str, event_type: str, state: str, details: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO hosting_runtime_events(site_id,event_type,state,details_json) VALUES (?,?,?,?)",
            (site_id, event_type, state, json.dumps(details, separators=(",", ":"), sort_keys=True)),
        )


def _latest_event(site_id: str, event_types: tuple[str, ...]) -> dict[str, Any] | None:
    placeholders = ",".join("?" for _ in event_types)
    with db() as connection:
        row = connection.execute(
            f"""SELECT id,event_type,state,details_json,created_at
                FROM hosting_runtime_events
                WHERE site_id=? AND event_type IN ({placeholders})
                ORDER BY id DESC LIMIT 1""",
            (site_id, *event_types),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["details"] = json.loads(item.pop("details_json") or "{}")
    except json.JSONDecodeError:
        item["details"] = {}
        item.pop("details_json", None)
    return item


def policy(site_id: str) -> dict[str, Any]:
    hosting_runtime.get_site(site_id)
    result = dict(DEFAULT_POLICY)
    event = _latest_event(site_id, ("hosting.health.policy.updated",))
    if event:
        details = event.get("details") or {}
        for key in DEFAULT_POLICY:
            if key in details:
                result[key] = details[key]
    result["contract"] = CONTRACT
    result["site_id"] = site_id
    return result


def update_policy(site_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    current = policy(site_id)
    next_policy = {key: current[key] for key in DEFAULT_POLICY}
    for key, value in dict(changes or {}).items():
        if key not in DEFAULT_POLICY:
            continue
        next_policy[key] = value
    next_policy["enabled"] = bool(next_policy["enabled"])
    next_policy["interval_seconds"] = max(30, min(int(next_policy["interval_seconds"]), 3600))
    next_policy["failure_threshold"] = max(1, min(int(next_policy["failure_threshold"]), 10))
    next_policy["max_recovery_attempts"] = max(1, min(int(next_policy["max_recovery_attempts"]), 5))
    next_policy["cooldown_seconds"] = max(30, min(int(next_policy["cooldown_seconds"]), 3600))
    next_policy["auto_reactivate"] = bool(next_policy["auto_reactivate"])
    next_policy["auto_rollback"] = bool(next_policy["auto_rollback"])
    next_policy["auto_restore"] = bool(next_policy["auto_restore"])
    _event(site_id, "hosting.health.policy.updated", site["state"], next_policy)
    return policy(site_id)


def _core_issues(site_id: str) -> list[str]:
    site = hosting_runtime.get_site(site_id)
    issues: list[str] = []
    if site["state"] == "failed":
        issues.append("site_failed")
    if site["state"] == "suspended":
        issues.append("site_suspended")
    try:
        sqlite = hosting_runtime.database_health(site_id)
        if not sqlite.get("healthy"):
            issues.append("sqlite_unhealthy")
    except Exception:
        issues.append("sqlite_unhealthy")
    try:
        serving = hosting_serving.runtime_health(site_id)
        if site["state"] == "active" and not serving.get("local_serving_ready"):
            issues.append("runtime_not_ready")
        if str(site.get("runtime_kind") or "").lower() == "php" and not serving.get("php_cgi_available"):
            issues.append("php_runtime_unavailable")
    except Exception:
        issues.append("runtime_not_ready")
    return list(dict.fromkeys(issues))


def _health_snapshot(site_id: str) -> dict[str, Any]:
    diagnostics = hosting_diagnostics.summary(site_id, window_minutes=15, recent_limit=0)
    core = _core_issues(site_id)
    transient = [
        code for code in diagnostics.get("issues", [])
        if code in {"recent_5xx", "recent_php_failures", "recent_slow_requests", "public_route_not_ready"}
    ]
    issues = list(dict.fromkeys(core + transient))
    return {
        "core_issues": core,
        "issues": issues,
        "diagnostics": {
            "requests_total": int(diagnostics.get("requests_total") or 0),
            "server_error_total": int(diagnostics.get("server_error_total") or 0),
            "php_failure_total": int(diagnostics.get("php_failure_total") or 0),
            "slow_request_total": int(diagnostics.get("slow_request_total") or 0),
            "runtime": diagnostics.get("runtime") or {},
            "route": diagnostics.get("route") or {},
            "sqlite": diagnostics.get("sqlite") or {},
            "last_deploy": diagnostics.get("last_deploy"),
        },
    }


def _open_incident(site_id: str, issues: list[str], consecutive_failures: int) -> str:
    latest = _latest_event(
        site_id,
        (
            "hosting.health.incident.opened",
            "hosting.health.incident.recovering",
            "hosting.health.incident.escalated",
            "hosting.health.incident.recovered",
        ),
    )
    if latest and latest["event_type"] != "hosting.health.incident.recovered":
        incident_id = str((latest.get("details") or {}).get("incident_id") or "")
        if incident_id:
            return incident_id
    incident_id = "hostinc_" + uuid.uuid4().hex[:24]
    _event(
        site_id,
        "hosting.health.incident.opened",
        "failing",
        {
            "incident_id": incident_id,
            "issues": issues,
            "consecutive_failures": consecutive_failures,
            "opened_at": _now(),
        },
    )
    return incident_id


def _cloud_allows_activation(site_id: str) -> bool:
    try:
        from . import hosting_cloud_control
        binding = hosting_cloud_control.binding_for_site(site_id)
    except Exception:
        binding = None
    return not binding or str(binding.get("desired_state") or "") == "active"


def _cooldown_active(site_id: str, cooldown_seconds: int) -> bool:
    with db() as connection:
        row = connection.execute(
            """SELECT created_at FROM hosting_runtime_events
               WHERE site_id=? AND event_type='hosting.health.recovery.action'
               ORDER BY id DESC LIMIT 1""",
            (site_id,),
        ).fetchone()
        if row is None:
            return False
        active = connection.execute(
            "SELECT datetime(?) > datetime('now', ?)",
            (row["created_at"], f"-{max(30, int(cooldown_seconds))} seconds"),
        ).fetchone()[0]
    return bool(active)


def _action_count(site_id: str, incident_id: str) -> int:
    with db() as connection:
        rows = connection.execute(
            """SELECT details_json FROM hosting_runtime_events
               WHERE site_id=? AND event_type='hosting.health.recovery.action'
               ORDER BY id DESC LIMIT 100""",
            (site_id,),
        ).fetchall()
    total = 0
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if str(details.get("incident_id") or "") == incident_id:
            total += 1
    return total


def _record_action(site_id: str, incident_id: str, action: str, status: str, details: dict[str, Any] | None = None) -> None:
    payload = {
        "incident_id": incident_id,
        "action": action,
        "status": status,
        "at": _now(),
        **(details or {}),
    }
    _event(site_id, "hosting.health.recovery.action", status, payload)


def _recover(site_id: str, incident_id: str, issues: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    deployment = hosting_deployment.deployment_status(site_id)
    action = "none"
    result: dict[str, Any] = {}

    if cfg["auto_reactivate"] and _cloud_allows_activation(site_id) and site["state"] in {"failed", "configured"} and deployment.get("active_release_id"):
        action = "reactivate"
        try:
            hosting_runtime.set_state(site_id, "active")
            result = {"state": "active"}
            _record_action(site_id, incident_id, action, "succeeded", result)
        except Exception as exc:
            _record_action(site_id, incident_id, action, "failed", {"error": str(exc)[:240]})
            return {"attempted": True, "action": action, "succeeded": False, "error": str(exc)[:240]}

    remaining = _core_issues(site_id)
    if remaining and cfg["auto_rollback"] and deployment.get("previous_release_id") and (
        "runtime_not_ready" in remaining or "php_runtime_unavailable" in remaining
    ):
        action = "rollback"
        try:
            recovery = hosting_recovery.create_recovery_point(site_id, reason=f"pre-auto-rollback:{incident_id}")
            rolled = hosting_deployment.rollback(site_id)
            result = {
                "release_id": rolled.get("release_id"),
                "previous_release_id": rolled.get("previous_release_id"),
                "recovery_id": recovery.get("recovery_id"),
            }
            _record_action(site_id, incident_id, action, "succeeded", result)
        except Exception as exc:
            _record_action(site_id, incident_id, action, "failed", {"error": str(exc)[:240]})
            return {"attempted": True, "action": action, "succeeded": False, "error": str(exc)[:240]}

    remaining = _core_issues(site_id)
    if remaining and cfg["auto_restore"] and "sqlite_unhealthy" in remaining:
        points = hosting_recovery.list_recovery_points(site_id)
        for point in points:
            try:
                verified = hosting_recovery.verify(site_id, point["recovery_id"])
                if verified.get("verified"):
                    action = "restore"
                    restored = hosting_recovery.restore(site_id, point["recovery_id"])
                    result = {
                        "recovery_id": point["recovery_id"],
                        "pre_restore_recovery_id": restored.get("pre_restore_recovery_id"),
                    }
                    _record_action(site_id, incident_id, action, "succeeded", result)
                    break
            except Exception:
                continue

    remaining = _core_issues(site_id)
    return {
        "attempted": action != "none",
        "action": action,
        "succeeded": action != "none" and not remaining,
        "remaining_core_issues": remaining,
        "no_safe_local_action": action == "none",
        **result,
    }


def evaluate(site_id: str, *, execute_recovery: bool = True) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    cfg = policy(site_id)
    snapshot = _health_snapshot(site_id)
    issues = snapshot["issues"]

    previous = _latest_event(site_id, ("hosting.health.check",))
    previous_details = (previous or {}).get("details") or {}
    previous_failures = int(previous_details.get("consecutive_failures") or 0)
    consecutive = previous_failures + 1 if issues else 0
    threshold = int(cfg["failure_threshold"])

    if not issues:
        health_state = "healthy"
    elif consecutive < threshold:
        health_state = "degraded"
    else:
        health_state = "failing"

    _event(
        site_id,
        "hosting.health.check",
        health_state,
        {
            "health_state": health_state,
            "issues": issues,
            "core_issues": snapshot["core_issues"],
            "consecutive_failures": consecutive,
            "threshold": threshold,
            "checked_at": _now(),
            "diagnostics": snapshot["diagnostics"],
        },
    )

    latest_incident = _latest_event(
        site_id,
        (
            "hosting.health.incident.opened",
            "hosting.health.incident.recovering",
            "hosting.health.incident.escalated",
            "hosting.health.incident.recovered",
        ),
    )
    if not issues:
        if latest_incident and latest_incident["event_type"] != "hosting.health.incident.recovered":
            incident_id = str((latest_incident.get("details") or {}).get("incident_id") or "")
            if incident_id:
                _event(site_id, "hosting.health.incident.recovered", "recovered", {
                    "incident_id": incident_id,
                    "recovered_at": _now(),
                    "issues": [],
                })
        return status(site_id)

    if consecutive < threshold or not execute_recovery or not cfg["enabled"]:
        return status(site_id)

    incident_id = _open_incident(site_id, issues, consecutive)
    attempts = _action_count(site_id, incident_id)
    if _cooldown_active(site_id, int(cfg["cooldown_seconds"])):
        _event(site_id, "hosting.health.incident.recovering", "recovering", {
            "incident_id": incident_id,
            "issues": issues,
            "attempts": attempts,
            "reason": "recovery_cooldown",
        })
        return status(site_id)
    if attempts >= int(cfg["max_recovery_attempts"]):
        _event(site_id, "hosting.health.incident.escalated", "escalated", {
            "incident_id": incident_id,
            "issues": issues,
            "attempts": attempts,
            "reason": "automatic_recovery_exhausted",
        })
        return status(site_id)

    recovery = _recover(site_id, incident_id, issues, cfg)
    core_after = _core_issues(site_id)
    if recovery.get("no_safe_local_action"):
        _event(site_id, "hosting.health.incident.escalated", "escalated", {
            "incident_id": incident_id,
            "issues": issues,
            "attempts": attempts,
            "reason": "no_safe_local_recovery",
            "cloud_authority_required": "public_route_not_ready" in issues,
        })
    elif recovery.get("attempted") and not core_after:
        _event(site_id, "hosting.health.incident.recovered", "recovered", {
            "incident_id": incident_id,
            "recovered_at": _now(),
            "action": recovery.get("action"),
        })
    elif not recovery.get("no_safe_local_action"):
        next_attempts = _action_count(site_id, incident_id)
        event_type = "hosting.health.incident.escalated" if next_attempts >= int(cfg["max_recovery_attempts"]) else "hosting.health.incident.recovering"
        state = "escalated" if event_type.endswith("escalated") else "recovering"
        _event(site_id, event_type, state, {
            "incident_id": incident_id,
            "issues": issues,
            "attempts": next_attempts,
            "recovery": recovery,
        })
    return status(site_id)


def status(site_id: str) -> dict[str, Any]:
    hosting_runtime.get_site(site_id)
    cfg = policy(site_id)
    check = _latest_event(site_id, ("hosting.health.check",))
    incident = _latest_event(
        site_id,
        (
            "hosting.health.incident.opened",
            "hosting.health.incident.recovering",
            "hosting.health.incident.escalated",
            "hosting.health.incident.recovered",
        ),
    )
    with db() as connection:
        rows = connection.execute(
            """SELECT event_type,state,details_json,created_at
               FROM hosting_runtime_events
               WHERE site_id=? AND event_type IN (
                 'hosting.health.recovery.action',
                 'hosting.health.incident.opened',
                 'hosting.health.incident.recovering',
                 'hosting.health.incident.recovered',
                 'hosting.health.incident.escalated'
               )
               ORDER BY id DESC LIMIT 30""",
            (site_id,),
        ).fetchall()
    history = []
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        history.append({
            "event_type": row["event_type"],
            "state": row["state"],
            "details": details,
            "created_at": row["created_at"],
        })

    health_state = str((check or {}).get("state") or "unknown")
    if incident:
        if incident["event_type"] == "hosting.health.incident.recovered":
            health_state = "recovered" if health_state != "healthy" else "healthy"
        else:
            health_state = str(incident.get("state") or health_state)
    return {
        "contract": CONTRACT,
        "site_id": site_id,
        "health_state": health_state,
        "policy": cfg,
        "last_check": check,
        "incident": incident,
        "history": history,
        "homeserver_authoritative": True,
    }


def _due(site_id: str, interval_seconds: int) -> bool:
    with db() as connection:
        row = connection.execute(
            """SELECT created_at FROM hosting_runtime_events
               WHERE site_id=? AND event_type='hosting.health.check'
               ORDER BY id DESC LIMIT 1""",
            (site_id,),
        ).fetchone()
        if row is None:
            return True
        due = connection.execute(
            "SELECT datetime(?) <= datetime('now', ?)",
            (row["created_at"], f"-{max(30, int(interval_seconds))} seconds"),
        ).fetchone()[0]
    return bool(due)


def scan_all() -> dict[str, Any]:
    checked = []
    errors = []
    for site in hosting_runtime.list_sites():
        site_id = str(site["site_id"])
        cfg = policy(site_id)
        if not cfg["enabled"] or not _due(site_id, int(cfg["interval_seconds"])):
            continue
        try:
            checked.append(evaluate(site_id, execute_recovery=True))
        except Exception as exc:
            errors.append({"site_id": site_id, "error": str(exc)[:240]})
    return {"contract": CONTRACT, "checked": checked, "errors": errors}


def _loop() -> None:
    while not _STOP.wait(5.0):
        try:
            scan_all()
        except Exception:
            continue


def start() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, name="hosting-health-recovery", daemon=True)
        _THREAD.start()


def stop() -> None:
    global _THREAD
    with _LOCK:
        _STOP.set()
        thread = _THREAD
        _THREAD = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


def public_capability() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "periodic_health_checks": True,
        "durable_incident_ledger": True,
        "bounded_recovery_attempts": True,
        "automatic_reactivation": True,
        "automatic_release_rollback": True,
        "verified_recovery_restore": True,
        "automatic_restore_default": False,
        "cloud_route_fault_escalation": True,
        "background_monitor": True,
        "homeserver_authoritative": True,
        "query_strings_retained": False,
        "request_bodies_retained": False,
        "authorization_headers_retained": False,
    }

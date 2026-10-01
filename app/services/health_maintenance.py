"""Section 31B: convert canonical health transitions into existing Activity Center notifications.

This module owns no repair actions and no second notification ledger.
"""
from __future__ import annotations

from . import health_repair, activity_center
from ..database import db
from threading import Lock

_SYNC_LOCK=Lock()

PREFIX = "health:"
MAX_ISSUES = 60


def sync_health_notifications() -> dict[str, int]:
    # Serialize recurring background and concurrent owner-triggered syncs.
    with _SYNC_LOCK:
        return _sync_locked()


def _sync_locked() -> dict[str, int]:
    # Health is already the canonical projection; never run repairs here.
    snapshot=health_repair.status()
    all_issues=snapshot["issues"]
    complete=bool(snapshot.get("snapshot_complete",True)) and len(all_issues)<=MAX_ISSUES
    issues=all_issues[:MAX_ISSUES]
    current={PREFIX+str(item["key"]) for item in all_issues}
    created = 0
    resolved = 0
    updated = 0
    with db() as connection:
        rows = connection.execute(
            "SELECT id,dedupe_key,level,archived_at,dismissed_at FROM notifications "
            "WHERE source='homeserver-health' AND dedupe_key LIKE 'health:%'"
        ).fetchall()
        existing = {str(row["dedupe_key"]): dict(row) for row in rows}
    for issue in issues:
        key = PREFIX + str(issue["key"])
        old = existing.get(key)
        severity = str(issue.get("severity") or "attention")
        level = "error" if severity in ("failed", "critical") else "warning"
        # Only notify on a new issue, escalation, or recurrence after resolution.
        if old and old["archived_at"] is None and old["level"] == level:
            continue
        repair = issue.get("repair") or {}
        description = (
            "An existing governed recovery action may be available. "
            "Review it in Agent Chat; owner approvals still apply."
            if repair.get("agent_can_execute") else
            "Diagnosis or owner review is needed; no trusted automatic repair is available."
        )
        result = activity_center.emit_notification(
            source="homeserver-health",
            title=str(issue.get("title") or "HomeServer maintenance issue")[:180],
            body=description,
            level=level,
            priority="urgent" if severity in ("failed", "critical") else "high",
            category="system",
            source_kind="system",
            source_key="homeserver-health",
            event_key="health.attention",
            dedupe_key=key,
            action_payload={"type":"open","target_view":"health"},
        )
        if result.get("suppressed"):
            continue
        if old:
            with db() as connection:
                # A resolved recurrence is genuinely new attention.
                if old["archived_at"] is not None or (old["level"]=="warning" and level=="error"):
                    # Owner dismissal silences an unchanged problem, but a new
                    # critical escalation must be visible again.
                    connection.execute(
                        "UPDATE notifications SET archived_at=NULL,dismissed_at=NULL,read_at=NULL WHERE id=?",
                        (int(old["id"]),),
                    )
            updated += 1
        else:
            created += 1
    for key, old in existing.items():
        if complete and key not in current and old["archived_at"] is None:
            with db() as connection:
                connection.execute(
                    "UPDATE notifications SET archived_at=CURRENT_TIMESTAMP WHERE id=? AND archived_at IS NULL",
                    (int(old["id"]),),
                )
            resolved += 1
    return {"created": created, "updated": updated, "resolved": resolved, "snapshot_complete":bool(complete)}

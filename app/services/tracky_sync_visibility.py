from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import (
    tracky_cross_site_presence,
    tracky_federation_operations,
    tracky_federation_reconciliation,
    tracky_federation_sync,
)

TRACKY_SYNC_VISIBILITY_VERSION = "2.80"
FEDERATION_SYNC_VISIBILITY_PROTOCOL = "physical_federation_sync_visibility.v1"
STATUS_RANK = {"current": 0, "unknown": 1, "suspect": 2, "reconciling": 3, "stale": 4, "partitioned": 5, "failed": 6}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _time_ms(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        numeric = float(value)
        if numeric > 0:
            return int(numeric if numeric > 100_000_000_000 else numeric * 1000)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _site_id(row: dict[str, Any] | None) -> str:
    row = row or {}
    return _text(row.get("id") or row.get("site_id"), 64).lower()


def _progress(local_revision: int, remote_revision: int) -> float:
    if remote_revision <= 0:
        return 1.0 if local_revision > 0 else 0.0
    return max(0.0, min(1.0, local_revision / remote_revision))


def _conflict_code(row: dict[str, Any]) -> str:
    local_revision = _number(row.get("local_revision"))
    remote_revision = _number(row.get("remote_revision"))
    local_epoch = _number(row.get("local_authority_epoch"))
    remote_epoch = _number(row.get("remote_authority_epoch"))
    local_fp = _text(row.get("local_fingerprint"), 128)
    remote_fp = _text(row.get("remote_fingerprint"), 128)
    status = _text(row.get("status") or "unknown", 24).lower()
    if local_revision > 0 and local_revision == remote_revision and local_fp and remote_fp and local_fp != remote_fp:
        return "same_revision_fingerprint_conflict"
    if local_epoch > 0 and remote_epoch > 0 and local_epoch != remote_epoch:
        return "authority_epoch_mismatch"
    if remote_revision > local_revision:
        return "revision_gap"
    if status == "partitioned":
        return "transport_partition"
    if status == "failed":
        return _text(row.get("last_error"), 120) or "reconciliation_failed"
    if status == "stale":
        return "stale_peer"
    if status == "reconciling":
        return _text(row.get("last_error"), 120) or "reconciling"
    if status == "suspect":
        return "peer_suspect"
    if status == "unknown":
        return "peer_unknown"
    return ""


def _message(row: dict[str, Any]) -> str:
    if row["is_local"]:
        return "Local authoritative site is current by local ownership."
    status = row["status"]
    if status == "current":
        return "Current; last reconciliation completed successfully." if row["last_reconciled_at"] else "Current; no reconciliation is required."
    if status == "partitioned":
        return "Partitioned; remote data remains labeled stale until authoritative reconciliation completes."
    if status == "reconciling":
        gap = row["revision_gap"]
        return f"Reconciling {gap} revision{'s' if gap != 1 else ''} from the origin site." if gap else "Reconciling authoritative site state."
    if status == "stale":
        return "Stale; remote semantic data must not be treated as current."
    if status == "failed":
        return f"Reconciliation failed closed: {row['last_error'] or 'unknown reconciliation error'}."
    if status == "suspect":
        return "Peer contact is suspect; freshness is degraded."
    return "Peer freshness is unknown until the origin site reports authoritative state."


def _normalize_site(
    site: dict[str, Any],
    peer: dict[str, Any],
    local_site: str,
    now_ms: int,
) -> dict[str, Any]:
    site_id = _site_id(site) or _text(peer.get("remote_site_id") or peer.get("site_id"), 64).lower()
    is_local = bool(site_id and site_id == local_site)
    federation = site.get("federation") if isinstance(site.get("federation"), dict) else {}
    raw_status = "current" if is_local else _text(peer.get("status") or federation.get("status") or "unknown", 24).lower()
    status = raw_status if raw_status in STATUS_RANK else "unknown"
    local_revision = _number(site.get("world_revision")) if is_local else _number(peer.get("local_revision"))
    remote_revision = local_revision if is_local else _number(peer.get("remote_revision"))
    authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
    local_epoch = _number(authority.get("epoch") or site.get("authority_epoch")) if is_local else _number(peer.get("local_authority_epoch"))
    remote_epoch = local_epoch if is_local else _number(peer.get("remote_authority_epoch"))
    stale_since = 0 if is_local else _time_ms(peer.get("stale_since") or federation.get("stale_since"))
    partitioned_at = 0 if is_local else _time_ms(peer.get("partitioned_at") or federation.get("partitioned_at"))
    reconciling_since = 0 if is_local else _time_ms(peer.get("reconciling_since"))
    last_contact_at = now_ms if is_local else _time_ms(peer.get("last_contact_at") or federation.get("last_contact_at"))
    last_reconciled_at = now_ms if is_local else _time_ms(peer.get("last_reconciled_at"))
    next_retry_at = 0 if is_local else _time_ms(peer.get("next_retry_at") or federation.get("next_retry_at"))
    revision_gap = max(0, remote_revision - local_revision)
    local_fp = "" if is_local else _text(peer.get("local_fingerprint"), 128)
    remote_fp = "" if is_local else _text(peer.get("remote_fingerprint"), 128)
    fingerprint_conflict = bool(
        not is_local
        and local_revision > 0
        and local_revision == remote_revision
        and local_fp
        and remote_fp
        and local_fp != remote_fp
    )
    epoch_mismatch = bool(not is_local and local_epoch > 0 and remote_epoch > 0 and local_epoch != remote_epoch)
    result = {
        "site_id": site_id,
        "label": _text(site.get("label") or site_id),
        "is_local": is_local,
        "status": status,
        "fresh": is_local or status == "current",
        "reconciliation_required": (
            not is_local
            and (
                bool(peer.get("reconciliation_required"))
                or status in {"partitioned", "reconciling", "stale", "failed", "unknown"}
                or revision_gap > 0
                or epoch_mismatch
                or fingerprint_conflict
            )
        ),
        "stale_since": stale_since,
        "stale_age_ms": max(0, now_ms - stale_since) if stale_since else 0,
        "partitioned_at": partitioned_at,
        "partition_age_ms": max(0, now_ms - partitioned_at) if partitioned_at else 0,
        "reconciling_since": reconciling_since,
        "reconciling_age_ms": max(0, now_ms - reconciling_since) if reconciling_since else 0,
        "last_contact_at": last_contact_at,
        "contact_age_ms": max(0, now_ms - last_contact_at) if last_contact_at else None,
        "last_reconciled_at": last_reconciled_at,
        "retry_count": 0 if is_local else _number(peer.get("retry_count") or federation.get("retry_count")),
        "next_retry_at": next_retry_at,
        "retry_due_in_ms": max(0, next_retry_at - now_ms) if next_retry_at else 0,
        "last_error": "" if is_local else _text(peer.get("last_error") or federation.get("last_error"), 240),
        "local_cursor": {"revision": local_revision, "fingerprint": local_fp, "authority_epoch": local_epoch},
        "remote_cursor": {"revision": remote_revision, "fingerprint": remote_fp, "authority_epoch": remote_epoch},
        "revision_gap": revision_gap,
        "authority_epoch_mismatch": epoch_mismatch,
        "fingerprint_conflict": fingerprint_conflict,
        "catch_up": {
            "applied_revision": local_revision,
            "target_revision": max(local_revision, remote_revision),
            "remaining_revisions": revision_gap,
            "progress": _progress(local_revision, max(local_revision, remote_revision)),
        },
        "conflict_code": "",
        "authority_assignment": "origin_only",
        "remote_authority_promotion": False,
    }
    result["conflict_code"] = _conflict_code({
        "status": status,
        "local_revision": local_revision,
        "remote_revision": remote_revision,
        "local_authority_epoch": local_epoch,
        "remote_authority_epoch": remote_epoch,
        "local_fingerprint": local_fp,
        "remote_fingerprint": remote_fp,
        "last_error": result["last_error"],
    })
    result["message"] = _message(result)
    return result


def _normalize_run(row: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    site_id = _text(row.get("remote_site_id") or row.get("site_id"), 64).lower()
    details_raw = row.get("details_json")
    if isinstance(details_raw, str):
        try:
            details = json.loads(details_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            details = {}
    else:
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
    return {
        "reconciliation_id": _text(row.get("reconciliation_id"), 180),
        "site_id": site_id,
        "site_label": labels.get(site_id) or site_id,
        "status": _text(row.get("status") or "unknown", 30).lower(),
        "request_mode": _text(row.get("request_mode") or "unknown", 30).lower(),
        "reason": _text(row.get("reason"), 160),
        "local_revision": _number(row.get("local_revision")),
        "remote_revision": _number(row.get("remote_revision")),
        "applied_revision": _number(row.get("applied_revision")),
        "authority_epoch": _number(row.get("authority_epoch")),
        "started_at": _time_ms(row.get("started_at") or row.get("created_at")),
        "completed_at": _time_ms(row.get("completed_at")),
        "fingerprint_conflict": bool(details.get("same_revision_fingerprint_conflict")),
        "authority_epoch_changed": bool(details.get("authority_epoch_changed")),
        "immutable": True,
    }


def build_report(
    operations: dict[str, Any],
    reconciliation: dict[str, Any],
    sync: dict[str, Any],
    *,
    cross_site_transitions: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
    operations = _copy(operations if isinstance(operations, dict) else {})
    reconciliation = _copy(reconciliation if isinstance(reconciliation, dict) else {})
    sync = _copy(sync if isinstance(sync, dict) else {})
    local_site = _text(reconciliation.get("local_site_id") or sync.get("local_site_id") or operations.get("local_site_id"), 64).lower()

    peer_map = {}
    for row in reconciliation.get("peers", []):
        if not isinstance(row, dict):
            continue
        site_id = _text(row.get("remote_site_id") or row.get("site_id"), 64).lower()
        if site_id:
            peer_map[site_id] = row
    op_map = {_site_id(row): row for row in operations.get("sites", []) if isinstance(row, dict) and _site_id(row)}
    ids = set(op_map) | set(peer_map)
    if local_site:
        ids.add(local_site)
    sites = [_normalize_site(op_map.get(site_id) or {"id": site_id}, peer_map.get(site_id) or {}, local_site, now_ms) for site_id in sorted(ids)]
    labels = {row["site_id"]: row["label"] for row in sites}

    runs = [
        _normalize_run(row, labels)
        for row in reconciliation.get("runs", [])
        if isinstance(row, dict)
    ]
    runs.sort(key=lambda row: -(row["started_at"] or row["completed_at"]))
    runs = runs[:100]

    worst = "current"
    for row in sites:
        if STATUS_RANK.get(row["status"], 1) > STATUS_RANK.get(worst, 0):
            worst = row["status"]
    alerts = [
        {
            "site_id": row["site_id"],
            "label": row["label"],
            "status": row["status"],
            "severity": "critical" if row["status"] in {"partitioned", "failed"} else "degraded",
            "message": row["message"],
            "stale_age_ms": row["stale_age_ms"],
            "revision_gap": row["revision_gap"],
            "retry_count": row["retry_count"],
            "last_error": row["last_error"],
        }
        for row in sites
        if not row["is_local"] and row["status"] != "current"
    ]

    transition_hints = []
    for item in cross_site_transitions or []:
        if not isinstance(item, dict) or not item.get("active"):
            continue
        source = item.get("source_site") if isinstance(item.get("source_site"), dict) else {}
        destination = item.get("destination_site") if isinstance(item.get("destination_site"), dict) else {}
        source_id = _text(source.get("site_id") or item.get("source_site_id"), 64).lower()
        destination_id = _text(destination.get("site_id") or item.get("destination_site_id"), 64).lower()
        source_sync = next((row for row in sites if row["site_id"] == source_id), None)
        destination_sync = next((row for row in sites if row["site_id"] == destination_id), None)
        transition_hints.append({
            "transition_id": _text(item.get("transition_id"), 160),
            "subject_label": _text(item.get("subject_label") or item.get("subject_id"), 160),
            "state": _text(item.get("state"), 40).lower(),
            "source_site_id": source_id,
            "destination_site_id": destination_id,
            "source_sync_status": (source_sync or {}).get("status", "unknown"),
            "destination_sync_status": (destination_sync or {}).get("status", "unknown"),
            "destination_presence_claim_blocked": bool(destination_sync and not destination_sync["fresh"] and item.get("state") != "arrived"),
            "note": (
                f"Destination federation data is {destination_sync['status']}; do not infer arrival from stale remote state."
                if destination_sync and not destination_sync["fresh"] else ""
            ),
        })

    return {
        "protocol": FEDERATION_SYNC_VISIBILITY_PROTOCOL,
        "version": TRACKY_SYNC_VISIBILITY_VERSION,
        "schema_version": 1,
        "generated_at": now_ms,
        "local_site_id": local_site,
        "overall_state": worst,
        "counts": {
            "sites": len(sites),
            "current": sum(1 for row in sites if row["status"] == "current"),
            "stale": sum(1 for row in sites if row["status"] in {"stale", "suspect", "unknown"}),
            "partitioned": sum(1 for row in sites if row["status"] == "partitioned"),
            "reconciling": sum(1 for row in sites if row["status"] == "reconciling"),
            "failed": sum(1 for row in sites if row["status"] == "failed"),
        },
        "sites": sites,
        "reconciliation_runs": runs,
        "transition_sync_hints": transition_hints,
        "agent_context": {
            "state": worst,
            "alerts": alerts[:16],
            "summary": "; ".join(f"{row['label']} is {row['status']}" for row in alerts[:6]) if alerts else "All known federation sites are current.",
            "no_remote_authority_promotion": True,
            "remote_freshness_requires_origin_confirmation": True,
        },
        "read_only": True,
        "semantic_only": True,
        "authority_assignment": "origin_only",
        "cloud_role": "relay_and_mirror_only",
        "cloud_can_mark_destination_current": False,
        "boundaries": [
            "origin-site-authority-only",
            "remote-staleness-is-explicit",
            "revision-gap-requires-authoritative-reconciliation",
            "authority-epoch-change-requires-revalidation",
            "same-revision-fingerprint-conflict-fails-closed",
            "retry-state-is-visible-but-not-cloud-controlled",
            "cloud-cannot-declare-a-destination-current",
        ],
    }


def current_report() -> dict[str, Any]:
    operations = tracky_federation_operations.current_report()
    reconciliation = tracky_federation_reconciliation.current_report()
    sync = tracky_federation_sync.status()
    reconciliation["local_site_id"] = sync.get("local_site_id") or operations.get("local_site_id") or ""
    try:
        transitions = tracky_cross_site_presence.current_report().get("active_transitions") or []
    except Exception:
        transitions = []
    return build_report(operations, reconciliation, sync, cross_site_transitions=transitions)


def cloud_projection() -> dict[str, Any]:
    report = current_report()
    return {
        **report,
        "reconciliation_runs": report.get("reconciliation_runs", [])[:50],
        "summary_only": True,
        "cloud_read_only": True,
        "cloud_can_mark_destination_current": False,
        "authority_mutation": False,
    }


def annotate_dashboard(dashboard: dict[str, Any], visibility: dict[str, Any] | None = None) -> dict[str, Any]:
    dashboard = _copy(dashboard if isinstance(dashboard, dict) else {})
    visibility = visibility if isinstance(visibility, dict) else current_report()
    selected = _text((dashboard.get("selected_site") or {}).get("site_id"), 64).lower()
    row = next((item for item in visibility.get("sites", []) if item.get("site_id") == selected), None)
    freshness = {
        "site_id": selected,
        "status": (row or {}).get("status", "unknown"),
        "fresh": bool((row or {}).get("fresh")),
        "stale_age_ms": int((row or {}).get("stale_age_ms") or 0),
        "reconciliation_required": bool((row or {}).get("reconciliation_required", True)),
        "revision_gap": int((row or {}).get("revision_gap") or 0),
        "authority_epoch_mismatch": bool((row or {}).get("authority_epoch_mismatch")),
        "fingerprint_conflict": bool((row or {}).get("fingerprint_conflict")),
        "message": (row or {}).get("message") or "Federation freshness is unknown.",
    }
    dashboard["federation_freshness"] = freshness
    dashboard.setdefault("selected_site", {})["federation_freshness"] = freshness
    for key in ("rooms", "people", "objects", "world_devices"):
        if isinstance(dashboard.get(key), list):
            dashboard[key] = [{**item, "federation_freshness": freshness} for item in dashboard[key] if isinstance(item, dict)]
    dashboard.setdefault("agent_context", {})["sync_state"] = freshness["status"]
    dashboard["agent_context"]["sync_message"] = freshness["message"]
    dashboard["agent_context"]["remote_freshness_verified"] = freshness["fresh"]
    dashboard["agent_context"]["no_remote_authority_promotion"] = True
    return dashboard


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_SYNC_VISIBILITY_VERSION,
        "protocol": FEDERATION_SYNC_VISIBILITY_PROTOCOL,
        "peer_states": ["unknown", "current", "suspect", "partitioned", "reconciling", "stale", "failed"],
        "revision_gap_visibility": True,
        "authority_epoch_visibility": True,
        "fingerprint_conflict_visibility": True,
        "retry_visibility": True,
        "immutable_reconciliation_history": True,
        "physical_world_freshness_annotations": True,
        "transition_sync_annotations": True,
        "agent_context": True,
        "read_only": True,
        "authority_mutation": False,
        "cloud_can_mark_destination_current": False,
    }

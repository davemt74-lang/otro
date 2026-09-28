from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db

TRACKY_FEDERATION_RECONCILIATION_VERSION = "2.78"
FEDERATION_RECONCILIATION_PROTOCOL = "physical_federation_reconciliation.v1"
PEER_STATES = {"unknown", "current", "suspect", "partitioned", "reconciling", "stale", "failed"}

SUSPECT_AFTER_SECONDS = 15
PARTITION_AFTER_SECONDS = 45
STALE_AFTER_SECONDS = 120
MAX_RETRIES = 5


class TrackyFederationReconciliationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrackyFederationReconciliationError(f"{label} must be a UUID.") from exc


def _text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _reconciliation_id(site_id: str, local_revision: int, remote_revision: int, epoch: int) -> str:
    material = f"{site_id}|{local_revision}|{remote_revision}|{epoch}"
    return "fr:" + site_id + ":" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _sync_cursor(site_id: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT last_received_revision,last_received_fingerprint,last_received_authority_epoch,last_received_at
            FROM tracky_federation_sync_peers
            WHERE remote_site_id=?
            """,
            (site_id,),
        ).fetchone()
    if row is None:
        return {"revision": 0, "fingerprint": "", "authority_epoch": 0, "received_at": ""}
    return {
        "revision": int(row["last_received_revision"] or 0),
        "fingerprint": str(row["last_received_fingerprint"] or ""),
        "authority_epoch": int(row["last_received_authority_epoch"] or 0),
        "received_at": str(row["last_received_at"] or ""),
    }


def _state_row(site_id: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM tracky_federation_reconciliation_state WHERE remote_site_id=?",
            (site_id,),
        ).fetchone()
    if row is None:
        return {
            "remote_site_id": site_id,
            "status": "unknown",
            "last_contact_at": "",
            "partitioned_at": "",
            "stale_since": "",
            "reconciling_since": "",
            "last_error": "",
            "retry_count": 0,
            "next_retry_at": "",
            "local_revision": 0,
            "local_fingerprint": "",
            "local_authority_epoch": 0,
            "remote_revision": 0,
            "remote_fingerprint": "",
            "remote_authority_epoch": 0,
            "last_reconciliation_id": "",
            "last_reconciled_at": "",
            "updated_at": "",
        }
    return dict(row)


def _upsert_state(site_id: str, **values: Any) -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    current = _state_row(site_id)
    merged = {**current, **values, "remote_site_id": site_id}
    status = str(merged.get("status") or "unknown")
    if status not in PEER_STATES:
        raise TrackyFederationReconciliationError("Federation reconciliation state is invalid.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_federation_reconciliation_state(
              remote_site_id,status,last_contact_at,partitioned_at,stale_since,reconciling_since,
              last_error,retry_count,next_retry_at,local_revision,local_fingerprint,
              local_authority_epoch,remote_revision,remote_fingerprint,remote_authority_epoch,
              last_reconciliation_id,last_reconciled_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(remote_site_id) DO UPDATE SET
              status=excluded.status,last_contact_at=excluded.last_contact_at,
              partitioned_at=excluded.partitioned_at,stale_since=excluded.stale_since,
              reconciling_since=excluded.reconciling_since,last_error=excluded.last_error,
              retry_count=excluded.retry_count,next_retry_at=excluded.next_retry_at,
              local_revision=excluded.local_revision,local_fingerprint=excluded.local_fingerprint,
              local_authority_epoch=excluded.local_authority_epoch,
              remote_revision=excluded.remote_revision,remote_fingerprint=excluded.remote_fingerprint,
              remote_authority_epoch=excluded.remote_authority_epoch,
              last_reconciliation_id=excluded.last_reconciliation_id,
              last_reconciled_at=excluded.last_reconciled_at,updated_at=CURRENT_TIMESTAMP
            """,
            (
                site_id, status, str(merged.get("last_contact_at") or ""),
                str(merged.get("partitioned_at") or ""), str(merged.get("stale_since") or ""),
                str(merged.get("reconciling_since") or ""), _text(merged.get("last_error"), 240),
                max(0, int(merged.get("retry_count") or 0)), str(merged.get("next_retry_at") or ""),
                max(0, int(merged.get("local_revision") or 0)), _text(merged.get("local_fingerprint"), 128),
                max(0, int(merged.get("local_authority_epoch") or 0)),
                max(0, int(merged.get("remote_revision") or 0)), _text(merged.get("remote_fingerprint"), 128),
                max(0, int(merged.get("remote_authority_epoch") or 0)),
                _text(merged.get("last_reconciliation_id"), 180),
                str(merged.get("last_reconciled_at") or ""),
            ),
        )
    return _state_row(site_id)


def note_peer_contact(site_id: str, remote_cursor: dict[str, Any], *, reason: str = "relay_contact") -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    local = _sync_cursor(site_id)
    remote = {
        "revision": max(0, int(remote_cursor.get("revision") or 0)),
        "fingerprint": _text(remote_cursor.get("fingerprint"), 128),
        "authority_epoch": max(0, int(remote_cursor.get("authority_epoch") or 0)),
    }
    current = _state_row(site_id)
    conflict = (
        remote["revision"] > 0
        and remote["revision"] == local["revision"]
        and remote["fingerprint"]
        and local["fingerprint"]
        and remote["fingerprint"] != local["fingerprint"]
    )
    epoch_mismatch = (
        remote["authority_epoch"] > 0
        and local["authority_epoch"] > 0
        and remote["authority_epoch"] != local["authority_epoch"]
    )
    gap = remote["revision"] > local["revision"]
    now = _now_iso()
    if conflict:
        status = "failed"
        error = "same_revision_fingerprint_conflict"
    elif gap or epoch_mismatch or current["status"] in {"partitioned", "stale", "failed", "reconciling"}:
        status = "reconciling"
        error = ""
    else:
        status = "current"
        error = ""
    return _upsert_state(
        site_id,
        status=status,
        last_contact_at=now,
        partitioned_at="" if status == "current" else current.get("partitioned_at", ""),
        stale_since="" if status == "current" else (current.get("stale_since") or now),
        reconciling_since=(current.get("reconciling_since") or now) if status == "reconciling" else "",
        last_error=error,
        retry_count=0 if status == "current" else int(current.get("retry_count") or 0),
        next_retry_at="",
        local_revision=local["revision"],
        local_fingerprint=local["fingerprint"],
        local_authority_epoch=local["authority_epoch"],
        remote_revision=remote["revision"],
        remote_fingerprint=remote["fingerprint"],
        remote_authority_epoch=remote["authority_epoch"],
        last_reconciled_at=now if status == "current" else current.get("last_reconciled_at", ""),
    )


def mark_partition(site_id: str, reason: str = "transport_partition") -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    current = _state_row(site_id)
    now = _now_iso()
    local = _sync_cursor(site_id)
    return _upsert_state(
        site_id,
        status="partitioned",
        partitioned_at=current.get("partitioned_at") or now,
        stale_since=current.get("stale_since") or now,
        last_error=_text(reason, 240) or "transport_partition",
        local_revision=local["revision"],
        local_fingerprint=local["fingerprint"],
        local_authority_epoch=local["authority_epoch"],
    )


def begin_reconciliation(site_id: str, remote_cursor: dict[str, Any], *, reason: str = "partition_recovery") -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    local = _sync_cursor(site_id)
    remote_revision = max(0, int(remote_cursor.get("revision") or 0))
    remote_fingerprint = _text(remote_cursor.get("fingerprint"), 128)
    remote_epoch = max(0, int(remote_cursor.get("authority_epoch") or 0))
    conflict = (
        remote_revision > 0
        and remote_revision == local["revision"]
        and remote_fingerprint
        and local["fingerprint"]
        and remote_fingerprint != local["fingerprint"]
    )
    epoch_mismatch = remote_epoch > 0 and local["authority_epoch"] > 0 and remote_epoch != local["authority_epoch"]
    full = remote_revision > local["revision"] or epoch_mismatch or conflict
    request_mode = "authoritative_full" if full else "cursor_verify"
    reconciliation_id = _reconciliation_id(site_id, local["revision"], remote_revision, remote_epoch)
    now = _now_iso()
    with db() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO tracky_federation_reconciliation_runs(
              reconciliation_id,remote_site_id,request_mode,status,reason,local_revision,
              remote_revision,authority_epoch,details_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                reconciliation_id, site_id, request_mode, "running", _text(reason, 120),
                local["revision"], remote_revision, remote_epoch,
                _json({
                    "semantic_only": True,
                    "authority_assignment": "origin_only",
                    "same_revision_fingerprint_conflict": conflict,
                    "authority_epoch_changed": epoch_mismatch,
                }),
            ),
        )
    state = _upsert_state(
        site_id,
        status="reconciling" if not conflict else "failed",
        stale_since=_state_row(site_id).get("stale_since") or now,
        reconciling_since=now if not conflict else "",
        last_error="same_revision_fingerprint_conflict" if conflict else "",
        local_revision=local["revision"],
        local_fingerprint=local["fingerprint"],
        local_authority_epoch=local["authority_epoch"],
        remote_revision=remote_revision,
        remote_fingerprint=remote_fingerprint,
        remote_authority_epoch=remote_epoch,
        last_reconciliation_id=reconciliation_id,
    )
    return {
        "protocol": FEDERATION_RECONCILIATION_PROTOCOL,
        "reconciliation_id": reconciliation_id,
        "remote_site_id": site_id,
        "request_mode": request_mode,
        "status": state["status"],
        "reason": reason,
        "semantic_only": True,
        "authority_assignment": "origin_only",
        "local_cursor": local,
        "remote_cursor": {
            "revision": remote_revision,
            "fingerprint": remote_fingerprint,
            "authority_epoch": remote_epoch,
        },
    }


def complete_reconciliation(site_id: str, remote_cursor: dict[str, Any], *, reconciliation_id: str = "") -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    local = _sync_cursor(site_id)
    remote_revision = max(0, int(remote_cursor.get("revision") or 0))
    remote_fingerprint = _text(remote_cursor.get("fingerprint"), 128)
    remote_epoch = max(0, int(remote_cursor.get("authority_epoch") or 0))
    current = _state_row(site_id)
    rid = _text(reconciliation_id or current.get("last_reconciliation_id"), 180)
    if remote_epoch > 0 and local["authority_epoch"] != remote_epoch:
        return fail_reconciliation(site_id, "authority_epoch_not_reconciled", reconciliation_id=rid)
    if local["revision"] < remote_revision:
        return _upsert_state(
            site_id,
            status="reconciling",
            last_error="reconciliation_incomplete",
            local_revision=local["revision"],
            local_fingerprint=local["fingerprint"],
            local_authority_epoch=local["authority_epoch"],
            remote_revision=remote_revision,
            remote_fingerprint=remote_fingerprint,
            remote_authority_epoch=remote_epoch,
        )
    if (
        local["revision"] == remote_revision
        and remote_fingerprint
        and local["fingerprint"]
        and local["fingerprint"] != remote_fingerprint
    ):
        return fail_reconciliation(site_id, "same_revision_fingerprint_conflict", reconciliation_id=rid)
    now = _now_iso()
    with db() as connection:
        if rid:
            connection.execute(
                """
                UPDATE tracky_federation_reconciliation_runs
                SET status='completed',applied_revision=?,completed_at=?,details_json=details_json
                WHERE reconciliation_id=?
                """,
                (local["revision"], now, rid),
            )
    return _upsert_state(
        site_id,
        status="current",
        last_contact_at=now,
        partitioned_at="",
        stale_since="",
        reconciling_since="",
        last_error="",
        retry_count=0,
        next_retry_at="",
        local_revision=local["revision"],
        local_fingerprint=local["fingerprint"],
        local_authority_epoch=local["authority_epoch"],
        remote_revision=max(remote_revision, local["revision"]),
        remote_fingerprint=remote_fingerprint or local["fingerprint"],
        remote_authority_epoch=remote_epoch or local["authority_epoch"],
        last_reconciled_at=now,
    )


def fail_reconciliation(site_id: str, reason: str, *, reconciliation_id: str = "") -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    current = _state_row(site_id)
    rid = _text(reconciliation_id or current.get("last_reconciliation_id"), 180)
    with db() as connection:
        if rid:
            connection.execute(
                """
                UPDATE tracky_federation_reconciliation_runs
                SET status='failed',reason=?,completed_at=?
                WHERE reconciliation_id=?
                """,
                (_text(reason, 120), _now_iso(), rid),
            )
    return _upsert_state(
        site_id,
        status="failed",
        stale_since=current.get("stale_since") or _now_iso(),
        reconciling_since="",
        last_error=_text(reason, 240),
    )


def process_remote_cursors(cursors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    for raw in cursors[:128]:
        if not isinstance(raw, dict):
            continue
        site_id = _uuid(raw.get("site_id"), "remote site id")
        state = note_peer_contact(site_id, raw, reason="cloud_cursor")
        reconciliation = None
        if state.get("status") in {"reconciling", "failed"}:
            reconciliation = begin_reconciliation(site_id, raw, reason="cloud_cursor_recovery")
        processed.append({
            "site_id": site_id,
            "status": state.get("status"),
            "reconciliation": reconciliation,
        })
    return processed


def mark_all_remote_partitioned(reason: str = "cloud_transport_failure") -> int:
    with db() as connection:
        rows = connection.execute(
            "SELECT remote_site_id FROM tracky_federation_sync_peers ORDER BY remote_site_id"
        ).fetchall()
    changed = 0
    for row in rows:
        site_id = str(row["remote_site_id"] or "")
        if not site_id:
            continue
        mark_partition(site_id, reason)
        changed += 1
    return changed


def schedule_retry(site_id: str, reason: str = "reconciliation_failed") -> dict[str, Any]:
    site_id = _uuid(site_id, "remote site id")
    current = _state_row(site_id)
    retry_count = max(0, int(current.get("retry_count") or 0)) + 1
    if retry_count > MAX_RETRIES:
        return _upsert_state(
            site_id,
            status="failed",
            retry_count=retry_count,
            next_retry_at="",
            stale_since=current.get("stale_since") or _now_iso(),
            last_error=_text(reason, 240),
        )
    delay = min(60, 2 ** max(0, retry_count - 1))
    retry_at = datetime.now(timezone.utc).timestamp() + delay
    next_retry_at = datetime.fromtimestamp(retry_at, timezone.utc).isoformat()
    return _upsert_state(
        site_id,
        status="reconciling",
        retry_count=retry_count,
        next_retry_at=next_retry_at,
        stale_since=current.get("stale_since") or _now_iso(),
        last_error=_text(reason, 240),
    )


def annotate_query_result(result: dict[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(result))
    uncertainty = list(output.get("uncertainty") or [])
    stale_count = 0
    from . import tracky_federation_sync
    local_site = tracky_federation_sync.local_site_id(auto_pin=False) or ""
    for item in output.get("results") or []:
        if not isinstance(item, dict):
            continue
        site_id = str(item.get("site_id") or "")
        if not site_id:
            continue
        if site_id == local_site:
            freshness = {
                "site_id": site_id,
                "status": "current",
                "fresh": True,
                "stale_since": "",
                "reconciliation_required": False,
            }
        else:
            state = _state_row(site_id)
            freshness = {
                "site_id": site_id,
                "status": state["status"],
                "fresh": state["status"] == "current",
                "stale_since": state["stale_since"],
                "reconciliation_required": state["status"] in {"partitioned", "reconciling", "stale", "failed", "unknown"},
            }
        item["federation_freshness"] = freshness
        if freshness["status"] in {"partitioned", "reconciling", "stale", "failed"}:
            stale_count += 1
    if stale_count and "federation_stale" not in uncertainty:
        uncertainty.append("federation_stale")
    if stale_count and output.get("status") == "ok":
        output["status"] = "partial"
    output["uncertainty"] = uncertainty
    output["freshness_enforced"] = True
    output["authority_mutation"] = False
    return output


def current_report() -> dict[str, Any]:
    with db() as connection:
        peers = [dict(row) for row in connection.execute(
            "SELECT * FROM tracky_federation_reconciliation_state ORDER BY remote_site_id"
        ).fetchall()]
        runs = [dict(row) for row in connection.execute(
            "SELECT * FROM tracky_federation_reconciliation_runs ORDER BY id DESC LIMIT 100"
        ).fetchall()]
    return {
        "protocol": FEDERATION_RECONCILIATION_PROTOCOL,
        "schema_version": 1,
        "peers": peers,
        "runs": runs,
        "semantic_only": True,
        "authority_assignment": "origin_only",
        "cloud_role": "relay_and_mirror_only",
        "boundaries": public_capability()["boundaries"],
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATION_RECONCILIATION_VERSION,
        "protocol": FEDERATION_RECONCILIATION_PROTOCOL,
        "peer_states": sorted(PEER_STATES),
        "stale_data_labeled": True,
        "full_snapshot_on_gap": True,
        "authority_epoch_revalidation": True,
        "same_revision_conflicts": "fail_closed",
        "bounded_retries": True,
        "semantic_only": True,
        "authority_assignment": "origin_only",
        "cloud_role": "relay_and_mirror_only",
        "boundaries": [
            "origin-site-authority-only",
            "partition-never-promotes-remote-or-cloud-authority",
            "stale-data-must-be-labeled",
            "revision-gap-requires-authoritative-reconciliation",
            "authority-epoch-change-requires-revalidation",
            "same-revision-fingerprint-conflict-fails-closed",
            "retries-are-bounded-and-backoff-controlled",
            "reconciliation-is-semantic-only",
            "cloud-remains-relay-and-mirror-only",
        ],
    }

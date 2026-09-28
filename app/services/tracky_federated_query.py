from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from ..database import db
from . import (
    tracky_federated_world,
    tracky_federation_policy,
    tracky_federation_reconciliation,
    tracky_federation_sync,
)

TRACKY_FEDERATED_QUERY_VERSION = "2.78"
FEDERATED_QUERY_PROTOCOL = "physical_federated_query.v1"
QUERY_INTENTS = {
    "current_state",
    "where_is",
    "last_seen",
    "history",
    "what_changed",
    "explain",
}
HISTORY_INTENTS = {"last_seen", "history", "what_changed", "explain"}
LOCATION_PREDICATES = {"located_in", "located_on", "present_in", "moving_between"}


class TrackyFederatedQueryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)


def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrackyFederatedQueryError(f"{label} must be a UUID.") from exc


def _text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _timestamp_ms(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return 0
    try:
        numeric = float(text)
        return max(0, int(numeric))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int(parsed.timestamp() * 1000))


def _local_site() -> str:
    site = tracky_federation_sync.local_site_id(auto_pin=False)
    if not site:
        raise TrackyFederatedQueryError("Local federation site is unresolved.", 409)
    return _uuid(site, "local site id")


def _site_ref(value: str) -> tuple[str, str] | None:
    text = _text(value, 320)
    if not text.startswith("site:") or "::" not in text:
        return None
    head, encoded = text[5:].split("::", 1)
    try:
        site = _uuid(head, "target site id")
    except TrackyFederatedQueryError:
        return None
    local_id = unquote(encoded)
    if not local_id:
        return None
    return site, local_id[:160]


def normalize_query(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyFederatedQueryError("Federated query must be an object.")
    local = _local_site()
    intent = _text(input.get("intent") or "current_state", 40).lower()
    if intent not in QUERY_INTENTS:
        raise TrackyFederatedQueryError("Federated query intent is unsupported.")
    raw_sites = input.get("site_ids")
    if isinstance(raw_sites, list):
        sites = [_uuid(item, "query site id") for item in raw_sites[:32]]
    elif input.get("site_id"):
        sites = [_uuid(input.get("site_id"), "query site id")]
    else:
        sites = [local]
    sites = list(dict.fromkeys(sites))
    target_ref = _text(input.get("target_ref") or input.get("entity_ref") or "", 320)
    target = _site_ref(target_ref) if target_ref else None
    target_site = target[0] if target else ""
    target_local = target[1] if target else _text(input.get("local_id") or "", 160)
    if target_ref and target is None:
        raise TrackyFederatedQueryError("Federated query target must be a site-qualified reference.")
    if target_site and target_site not in sites:
        sites.append(target_site)
    try:
        since_ms = max(0, int(input.get("since_ms") or 0))
        until_ms = max(0, int(input.get("until_ms") or 0))
        limit = max(1, min(100, int(input.get("limit") or 50)))
    except (TypeError, ValueError) as exc:
        raise TrackyFederatedQueryError("Federated query range or limit is invalid.") from exc
    if since_ms and until_ms and since_ms > until_ms:
        raise TrackyFederatedQueryError("Federated query time range is invalid.")
    normalized = {
        "protocol": FEDERATED_QUERY_PROTOCOL,
        "schema_version": 1,
        "query_id": _text(input.get("query_id"), 128),
        "intent": intent,
        "local_site_id": local,
        "site_ids": sites,
        "target_ref": target_ref,
        "target_site_id": target_site,
        "target_local_id": target_local,
        "since_ms": since_ms,
        "until_ms": until_ms,
        "limit": limit,
        "semantic_only": True,
        "read_only": True,
    }
    if not normalized["query_id"]:
        normalized["query_id"] = "fq:" + _fingerprint(normalized)[:24]
    return normalized


def _permission(source: str, destination: str, scope: str) -> dict[str, Any]:
    if source == destination:
        return {"allowed": True, "reason": "local_site"}
    return tracky_federation_policy.permission_decision(source, destination, scope)


def _access(query: dict[str, Any], site_id: str) -> dict[str, Any]:
    local = query["local_site_id"]
    world = _permission(site_id, local, "semantic_world_read")
    if not world.get("allowed"):
        return {"allowed": False, "reason": world.get("reason") or "semantic_world_read_denied"}
    history = None
    if query["intent"] in HISTORY_INTENTS:
        history = _permission(site_id, local, "history_query")
        if not history.get("allowed"):
            return {"allowed": False, "reason": history.get("reason") or "history_query_denied"}
    return {
        "allowed": True,
        "reason": "local_site" if site_id == local else (
            "explicit_history_grant" if history else "explicit_world_grant"
        ),
        "world": world,
        "history": history,
    }


def _current_fragment(site_id: str, local_site_id: str) -> dict[str, Any] | None:
    report = tracky_federated_world.current_report(site_id)
    sites = report.get("sites") if isinstance(report.get("sites"), list) else []
    if not sites:
        return None
    fragment = sites[0]
    if site_id == local_site_id:
        return fragment
    return tracky_federation_policy.filter_world_fragment(site_id, local_site_id, fragment)


def _history(site_id: str, query: dict[str, Any]) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,site_id,world_revision,fingerprint,authority_device_id,authority_epoch,
                   topology_revision,observed_at,fragment_json,source,privacy_redaction,created_at
            FROM tracky_federated_world_history
            WHERE site_id=?
            ORDER BY world_revision ASC,id ASC
            LIMIT 500
            """,
            (site_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            fragment = json.loads(row["fragment_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(fragment, dict):
            continue
        if site_id != query["local_site_id"]:
            fragment = tracky_federation_policy.filter_world_fragment(
                site_id, query["local_site_id"], fragment
            )
            if fragment is None:
                continue
        observed_ms = _timestamp_ms(fragment.get("observed_at"))
        if query["since_ms"] and observed_ms < query["since_ms"]:
            continue
        if query["until_ms"] and observed_ms > query["until_ms"]:
            continue
        out.append({
            "snapshot_id": f"history:{site_id}:{int(row['world_revision'])}:{str(row['fingerprint'])[:16]}",
            "site_id": site_id,
            "world_revision": int(row["world_revision"] or 0),
            "authority_device_id": str(row["authority_device_id"] or ""),
            "authority_epoch": int(row["authority_epoch"] or 0),
            "topology_revision": int(row["topology_revision"] or 0),
            "observed_at_ms": observed_ms,
            "fingerprint": str(row["fingerprint"] or ""),
            "source": str(row["source"] or ""),
            "privacy_redaction": bool(row["privacy_redaction"]),
            "fragment": fragment,
        })
    return out[-query["limit"]:]


def _entity(fragment: dict[str, Any] | None, local_id: str) -> dict[str, Any] | None:
    if not fragment or not local_id:
        return None
    for item in fragment.get("entities", []):
        if isinstance(item, dict) and str(item.get("local_id") or "") == local_id:
            return item
    return None


def _relations(fragment: dict[str, Any] | None, local_id: str) -> list[dict[str, Any]]:
    if not fragment or not local_id:
        return []
    return [
        item for item in fragment.get("relations", [])
        if isinstance(item, dict)
        and local_id in {
            str(item.get("subject_local_id") or ""),
            str(item.get("object_local_id") or ""),
        }
    ][:100]


def _location(fragment: dict[str, Any] | None, local_id: str) -> dict[str, Any] | None:
    candidates = [
        item for item in _relations(fragment, local_id)
        if str(item.get("subject_local_id") or "") == local_id
        and str(item.get("predicate") or "") in LOCATION_PREDICATES
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: int(item.get("as_of") or 0), reverse=True)
    item = candidates[0]
    return {
        "predicate": str(item.get("predicate") or ""),
        "object_local_id": str(item.get("object_local_id") or ""),
        "object_ref": str(item.get("object_ref") or ""),
        "confidence": float(item.get("confidence") or 0),
        "temporal_state": str(item.get("temporal_state") or "unknown"),
        "source_event_id": str(item.get("source_event_id") or ""),
        "as_of": int(item.get("as_of") or 0),
    }


def _evidence(snapshot: dict[str, Any], access: dict[str, Any]) -> dict[str, Any]:
    return {
        "site_id": snapshot["site_id"],
        "world_revision": snapshot["world_revision"],
        "authority_device_id": snapshot["authority_device_id"],
        "authority_epoch": snapshot["authority_epoch"],
        "observed_at_ms": snapshot["observed_at_ms"],
        "fingerprint": snapshot["fingerprint"],
        "policy_basis": access["reason"],
    }


def _diff(first: dict[str, Any], last: dict[str, Any]) -> dict[str, Any]:
    before = {
        str(item.get("local_id") or ""): item
        for item in first.get("entities", [])
        if isinstance(item, dict) and item.get("local_id")
    }
    after = {
        str(item.get("local_id") or ""): item
        for item in last.get("entities", [])
        if isinstance(item, dict) and item.get("local_id")
    }
    added = [
        {"local_id": key, "type": value.get("type"), "label": value.get("label")}
        for key, value in after.items() if key not in before
    ]
    removed = [
        {"local_id": key, "type": value.get("type"), "label": value.get("label")}
        for key, value in before.items() if key not in after
    ]
    changed = [
        {"local_id": key, "before": before[key], "after": after[key]}
        for key in sorted(set(before) & set(after))
        if _json(before[key]) != _json(after[key])
    ]
    return {
        "added": added[:50],
        "removed": removed[:50],
        "changed": changed[:50],
    }


def _audit(query: dict[str, Any], result: dict[str, Any]) -> None:
    compact = {
        "protocol": result["protocol"],
        "query_id": result["query_id"],
        "intent": result["intent"],
        "status": result["status"],
        "denied": result["denied"],
        "confidence": result["confidence"],
    }
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_federated_query_audit(
              query_id,intent,local_site_id,requested_sites_json,target_ref,status,
              denied_json,result_fingerprint
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                query["query_id"], query["intent"], query["local_site_id"],
                _json(query["site_ids"]), query["target_ref"], result["status"],
                _json(result["denied"]), _fingerprint(compact),
            ),
        )
        connection.execute(
            """
            DELETE FROM tracky_federated_query_audit
            WHERE id NOT IN (
              SELECT id FROM tracky_federated_query_audit
              ORDER BY id DESC LIMIT 500
            )
            """
        )


def execute_query(input: dict[str, Any]) -> dict[str, Any]:
    query = normalize_query(input)
    results: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    for site_id in query["site_ids"]:
        access = _access(query, site_id)
        if not access["allowed"]:
            denied.append({"site_id": site_id, "reason": access["reason"]})
            continue
        local_id = query["target_local_id"]
        if query["target_site_id"] and query["target_site_id"] != site_id:
            local_id = ""
        if site_id != query["local_site_id"] and local_id.lower().startswith("person:"):
            denied.append({
                "site_id": site_id,
                "reason": "person_query_requires_identity_continuity",
            })
            continue

        current = _current_fragment(site_id, query["local_site_id"])
        history = _history(site_id, query) if query["intent"] in HISTORY_INTENTS else []
        data: dict[str, Any] | None = None

        if query["intent"] == "current_state":
            data = {"fragment": current} if current else None
        elif query["intent"] == "where_is":
            entity = _entity(current, local_id)
            data = {
                "entity": entity,
                "location": _location(current, local_id),
            } if entity else None
        elif query["intent"] == "last_seen":
            snapshots = list(reversed(history))
            found = next((item for item in snapshots if _entity(item["fragment"], local_id)), None)
            data = {
                "entity": _entity(found["fragment"], local_id),
                "location": _location(found["fragment"], local_id),
                "snapshot": _evidence(found, access),
            } if found else None
        elif query["intent"] == "history":
            snapshots = [
                item for item in history
                if not local_id or _entity(item["fragment"], local_id)
            ]
            data = {
                "snapshots": [
                    {
                        **_evidence(item, access),
                        "entity": _entity(item["fragment"], local_id) if local_id else None,
                        "relations": _relations(item["fragment"], local_id) if local_id else [],
                        "fragment": None if local_id else item["fragment"],
                    }
                    for item in snapshots[-query["limit"]:]
                ]
            }
        elif query["intent"] == "what_changed":
            if len(history) >= 2:
                data = {
                    "from": _evidence(history[0], access),
                    "to": _evidence(history[-1], access),
                    "changes": _diff(history[0]["fragment"], history[-1]["fragment"]),
                }
            else:
                data = {
                    "from": _evidence(history[0], access) if history else None,
                    "to": None,
                    "changes": {"added": [], "removed": [], "changed": []},
                }
        elif query["intent"] == "explain":
            latest = history[-1] if history else None
            data = {
                "current_entity": _entity(current, local_id) if local_id else None,
                "current_relations": _relations(current, local_id) if local_id else [],
                "provenance": [_evidence(latest, access)] if latest else [],
                "policy": {
                    "reason": access["reason"],
                    "world": access.get("world"),
                    "history": access.get("history"),
                },
            }

        results.append({
            "site_id": site_id,
            "access": "allowed" if data is not None else "no_evidence",
            "data": data,
        })

    evidence_count = len([item for item in results if item["data"] is not None])
    status = (
        "partial" if evidence_count and denied
        else "ok" if evidence_count
        else "denied" if denied
        else "unknown"
    )
    result = {
        "protocol": FEDERATED_QUERY_PROTOCOL,
        "schema_version": 1,
        "query_id": query["query_id"],
        "intent": query["intent"],
        "status": status,
        "results": results,
        "denied": denied,
        "confidence": min(1.0, evidence_count / max(1, len(query["site_ids"]))) if evidence_count else 0.0,
        "uncertainty": (
            (["no_matching_evidence"] if status == "unknown" else [])
            + (["policy_limited"] if denied else [])
        ),
        "explainability": {
            "local_site_id": query["local_site_id"],
            "requested_sites": query["site_ids"],
            "history_permission_required": query["intent"] in HISTORY_INTENTS,
            "no_location_invention": True,
            "person_world_federation": False,
            "authority_mutation": False,
        },
        "semantic_only": True,
        "read_only": True,
        "cloud_can_answer_from_mirrors_only": True,
    }
    result = tracky_federation_reconciliation.annotate_query_result(result)
    _audit(query, result)
    return result


def recent_query_audit(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(100, int(limit)))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT query_id,intent,local_site_id,requested_sites_json,target_ref,status,
                   denied_json,result_fingerprint,created_at
            FROM tracky_federated_query_audit
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        for key in ("requested_sites_json", "denied_json"):
            try:
                item[key.removesuffix("_json")] = json.loads(item.pop(key) or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                item[key.removesuffix("_json")] = []
                item.pop(key, None)
        out.append(item)
    return {
        "protocol": FEDERATED_QUERY_PROTOCOL,
        "queries": out,
        "read_only": True,
        "semantic_only": True,
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATED_QUERY_VERSION,
        "protocol": FEDERATED_QUERY_PROTOCOL,
        "intents": sorted(QUERY_INTENTS),
        "current_world_scope": "semantic_world_read",
        "history_scope": "history_query",
        "deny_by_default": True,
        "site_qualified_refs": True,
        "person_world_federation": False,
        "raw_perception": False,
        "authority_mutation": False,
        "query_audit_retention": 500,
        "cloud_role": "mirror_query_only",
        "boundaries": [
            "query-read-only",
            "history-deny-by-default",
            "site-qualified-references",
            "person-query-via-identity-continuity",
            "no-location-invention",
            "raw-perception-never-queryable",
            "cloud-mirror-only",
        ],
    }

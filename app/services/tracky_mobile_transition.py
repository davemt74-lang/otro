from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from typing import Any

from ..database import db

TRACKY_MOBILE_TRANSITION_VERSION = "2.78"
MOBILE_TRANSITION_PROTOCOL = "physical_mobile_transition.v1"
STATES = {"departing","in_transit","arriving","arrived","uncertain","offline","temporary_context","canceled"}
SUBJECT_KINDS = {"mobile_device","explicit_continuity_subject"}
FORBIDDEN_KEY_RE = re.compile(r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|embedding|embeddings|blob|bytes|pixels|file_path|filesystem_path|camera_uri)(?:$|_)", re.I)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,159}$")

class TrackyMobileTransitionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrackyMobileTransitionError(f"{label} must be a UUID.") from exc

def _text(value: Any, limit: int = 200, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not text:
        raise TrackyMobileTransitionError(f"{label} is required.")
    return text

def _id(value: Any, label: str) -> str:
    text = _text(value, 160, required=True, label=label)
    if not ID_RE.fullmatch(text):
        raise TrackyMobileTransitionError(f"{label} is invalid.")
    return text

def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

def _assert_semantic(value: Any, path: str = "payload", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyMobileTransitionError("Mobile transition payload nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 128:
            raise TrackyMobileTransitionError("Mobile transition object is too large.")
        for key, child in value.items():
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise TrackyMobileTransitionError(f"Mobile transition cannot retain raw perception data at {path}.{key}.", 422)
            _assert_semantic(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        if len(value) > 512:
            raise TrackyMobileTransitionError("Mobile transition list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
    elif isinstance(value, str) and len(value) > 20000:
        raise TrackyMobileTransitionError("Mobile transition string value is too large.")

def _json(value: Any) -> str:
    _assert_semantic(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def _site_exists(connection: Any, site_id: str) -> bool:
    return connection.execute("SELECT 1 FROM tracky_sites WHERE site_id=?", (site_id,)).fetchone() is not None

def _local_site_id() -> str | None:
    try:
        from . import tracky_federation_sync
        return tracky_federation_sync.local_site_id(auto_pin=False)
    except Exception:
        return None

def _temporary_context(value: Any) -> dict[str, Any] | None:
    if value in (None, {}, []):
        return None
    if not isinstance(value, dict):
        raise TrackyMobileTransitionError("temporary_context must be an object.")
    _assert_semantic(value, "temporary_context")
    if value.get("site_authority") not in (False, 0, None):
        raise TrackyMobileTransitionError("Temporary mobile context cannot have site authority.")
    if value.get("durable_site") not in (False, 0, None):
        raise TrackyMobileTransitionError("Temporary mobile context cannot become a durable site.")
    context_id = _id(value.get("id") or "temporary-context", "temporary context id")
    return {
        "id": context_id,
        "label": _text(value.get("label") or "Temporary context", 160),
        "observed_at": max(0, int(value.get("observed_at") or 0)),
        "confidence": _confidence(value.get("confidence")),
        "durable_site": False,
        "site_authority": False,
    }

def normalize_transition(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyMobileTransitionError("Mobile transition must be an object.")
    _assert_semantic(input, "transition")
    transition_id = _id(input.get("transition_id"), "transition_id")
    subject_kind = _text(input.get("subject_kind") or "mobile_device", 40).lower()
    if subject_kind not in SUBJECT_KINDS:
        raise TrackyMobileTransitionError("Mobile transition subject kind is unsupported.")
    raw_subject = input.get("subject_id")
    subject_id = _uuid(raw_subject, "mobile device id") if subject_kind == "mobile_device" else _id(raw_subject, "continuity subject id")
    subject_scope = "stable_mobile_device" if subject_kind == "mobile_device" else "explicit_continuity_subject"
    if input.get("subject_scope") and str(input.get("subject_scope")) != subject_scope:
        raise TrackyMobileTransitionError("Mobile transition subject scope is inconsistent.")
    state = _text(input.get("state"), 40).lower()
    if state not in STATES:
        raise TrackyMobileTransitionError("Mobile transition state is unsupported.")
    source_site_id = _uuid(input.get("source_site_id"), "source_site_id")
    destination_site_id = _uuid(input.get("destination_site_id"), "destination_site_id") if input.get("destination_site_id") else ""
    if destination_site_id and destination_site_id == source_site_id:
        raise TrackyMobileTransitionError("Mobile transition destination must differ from source site.")
    identity_linking = bool(input.get("identity_linking"))
    if identity_linking:
        raise TrackyMobileTransitionError("Cross-site identity linking is deferred to V2.78 Section 5.")
    evidence = list(input.get("evidence") or [])
    if len(evidence) > 256:
        raise TrackyMobileTransitionError("Mobile transition evidence exceeds the limit.")
    _assert_semantic(evidence, "transition.evidence")
    temporary_context = _temporary_context(input.get("temporary_context"))
    normalized = {
        "transition_id": transition_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "subject_scope": subject_scope,
        "source_site_id": source_site_id,
        "destination_site_id": destination_site_id,
        "state": state,
        "previous_state": _text(input.get("previous_state"), 40),
        "resume_state": _text(input.get("resume_state"), 40),
        "state_reason": _text(input.get("state_reason"), 200),
        "confidence": _confidence(input.get("confidence")),
        "destination_confidence": _confidence(input.get("destination_confidence")),
        "temporary_context": temporary_context,
        "evidence": evidence,
        "revision": max(1, int(input.get("revision") or 1)),
        "identity_linking": False,
        "authority_scope": "source_site",
        "started_at": max(0, int(input.get("started_at") or 0)),
        "state_changed_at": max(0, int(input.get("state_changed_at") or 0)),
        "updated_at": max(0, int(input.get("updated_at") or 0)),
        "arrived_at": int(input["arrived_at"]) if input.get("arrived_at") is not None else None,
        "canceled_at": int(input["canceled_at"]) if input.get("canceled_at") is not None else None,
        "offline_since": int(input["offline_since"]) if input.get("offline_since") is not None else None,
    }
    material = dict(normalized)
    normalized["fingerprint"] = hashlib.sha256(_json(material).encode("utf-8")).hexdigest()
    return normalized

def normalize_projection(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyMobileTransitionError("Mobile transition projection must be an object.")
    _assert_semantic(input, "mobile_transitions")
    if str(input.get("protocol") or "") != MOBILE_TRANSITION_PROTOCOL:
        raise TrackyMobileTransitionError("Mobile transition protocol is unsupported.")
    if bool(input.get("identity_linking")):
        raise TrackyMobileTransitionError("Cross-site identity linking is deferred to V2.78 Section 5.")
    transitions = list(input.get("transitions") or [])
    if len(transitions) > 256:
        raise TrackyMobileTransitionError("Mobile transition projection exceeds the limit.")
    return {
        "protocol": MOBILE_TRANSITION_PROTOCOL,
        "schema_version": 1,
        "generated_at": max(0, int(input.get("generated_at") or 0)),
        "transitions": [normalize_transition(item) for item in transitions if isinstance(item, dict)],
        "identity_linking": False,
        "semantic_only": True,
        "cloud_read_only": True,
        "authority_assignment": "local_only",
        "person_object_identity_linking": False,
        "temporary_context_site_authority": False,
    }

def ingest_projection(input: dict[str, Any], *, source: str = "tracky", origin_role: str = "local_authority") -> dict[str, Any]:
    if origin_role not in {"local_authority","cloud_mirror"}:
        raise TrackyMobileTransitionError("Mobile transition origin role is invalid.")
    projection = normalize_projection(input)
    local_site = _local_site_id()
    changed = stale = idempotent = 0
    for transition in projection["transitions"]:
        if origin_role == "local_authority" and local_site and transition["source_site_id"] != local_site:
            raise TrackyMobileTransitionError("Local HomeServer may originate only transitions owned by its local site.", 409)
        if origin_role == "cloud_mirror" and local_site and transition["destination_site_id"] and transition["destination_site_id"] != local_site:
            raise TrackyMobileTransitionError("Cloud mobile transition mirror is routed to a different destination site.", 409)

        with db() as connection:
            if not _site_exists(connection, transition["source_site_id"]):
                raise TrackyMobileTransitionError("Mobile transition source site is not registered.", 409)
            if transition["destination_site_id"] and not _site_exists(connection, transition["destination_site_id"]):
                raise TrackyMobileTransitionError("Mobile transition destination site is not registered.", 409)
            prior = connection.execute(
                "SELECT revision,fingerprint FROM tracky_mobile_transitions WHERE transition_id=?",
                (transition["transition_id"],),
            ).fetchone()
            if prior is not None and transition["revision"] < int(prior["revision"]):
                stale += 1
                continue
            if prior is not None and transition["revision"] == int(prior["revision"]):
                if str(prior["fingerprint"]) != transition["fingerprint"]:
                    raise TrackyMobileTransitionError("Mobile transition revision conflicts with existing state.", 409)
                idempotent += 1
                continue

            active = connection.execute(
                """
                SELECT transition_id FROM tracky_mobile_transitions
                WHERE subject_kind=? AND subject_id=? AND state NOT IN ('arrived','canceled')
                  AND transition_id<>?
                LIMIT 1
                """,
                (transition["subject_kind"], transition["subject_id"], transition["transition_id"]),
            ).fetchone()
            if active is not None and transition["state"] not in {"arrived","canceled"}:
                raise TrackyMobileTransitionError("Mobile transition subject already has a different active transition.", 409)

            temp_json = _json(transition["temporary_context"])
            evidence_json = _json(transition["evidence"])
            connection.execute(
                """
                INSERT INTO tracky_mobile_transitions(
                  transition_id,subject_kind,subject_id,subject_scope,source_site_id,destination_site_id,
                  state,previous_state,resume_state,state_reason,confidence,destination_confidence,
                  temporary_context_json,evidence_json,revision,identity_linking,authority_scope,origin_role,
                  fingerprint,started_at,state_changed_at,observed_updated_at,arrived_at,canceled_at,offline_since
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(transition_id) DO UPDATE SET
                  subject_kind=excluded.subject_kind,subject_id=excluded.subject_id,subject_scope=excluded.subject_scope,
                  source_site_id=excluded.source_site_id,destination_site_id=excluded.destination_site_id,
                  state=excluded.state,previous_state=excluded.previous_state,resume_state=excluded.resume_state,
                  state_reason=excluded.state_reason,confidence=excluded.confidence,
                  destination_confidence=excluded.destination_confidence,
                  temporary_context_json=excluded.temporary_context_json,evidence_json=excluded.evidence_json,
                  revision=excluded.revision,identity_linking=0,authority_scope='source_site',
                  origin_role=excluded.origin_role,fingerprint=excluded.fingerprint,
                  started_at=excluded.started_at,state_changed_at=excluded.state_changed_at,
                  observed_updated_at=excluded.observed_updated_at,arrived_at=excluded.arrived_at,
                  canceled_at=excluded.canceled_at,offline_since=excluded.offline_since,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    transition["transition_id"], transition["subject_kind"], transition["subject_id"],
                    transition["subject_scope"], transition["source_site_id"],
                    transition["destination_site_id"] or None, transition["state"],
                    transition["previous_state"] or None, transition["resume_state"] or None,
                    transition["state_reason"], transition["confidence"], transition["destination_confidence"],
                    temp_json, evidence_json, transition["revision"], 0, "source_site", origin_role,
                    transition["fingerprint"], transition["started_at"], transition["state_changed_at"],
                    transition["updated_at"], transition["arrived_at"], transition["canceled_at"],
                    transition["offline_since"],
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO tracky_mobile_transition_history(
                  transition_id,revision,state,source,origin_role,snapshot_json,fingerprint
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    transition["transition_id"], transition["revision"], transition["state"],
                    _text(source, 80) or "tracky", origin_role, _json(transition), transition["fingerprint"],
                ),
            )
            changed += 1
    return {"accepted": True, "changed": changed, "stale": stale, "idempotent": idempotent}

def _row_transition(row: Any) -> dict[str, Any]:
    try:
        temp = json.loads(row["temporary_context_json"] or "null")
    except (TypeError, ValueError, json.JSONDecodeError):
        temp = None
    try:
        evidence = json.loads(row["evidence_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        evidence = []
    return {
        "transition_id": str(row["transition_id"]),
        "subject_kind": str(row["subject_kind"]),
        "subject_id": str(row["subject_id"]),
        "subject_scope": str(row["subject_scope"]),
        "source_site_id": str(row["source_site_id"]),
        "destination_site_id": str(row["destination_site_id"] or ""),
        "state": str(row["state"]),
        "previous_state": str(row["previous_state"] or ""),
        "resume_state": str(row["resume_state"] or ""),
        "state_reason": str(row["state_reason"] or ""),
        "confidence": float(row["confidence"] or 0),
        "destination_confidence": float(row["destination_confidence"] or 0),
        "temporary_context": temp if isinstance(temp, dict) else None,
        "evidence": evidence if isinstance(evidence, list) else [],
        "revision": int(row["revision"] or 0),
        "identity_linking": False,
        "authority_scope": "source_site",
        "origin_role": str(row["origin_role"] or "local_authority"),
        "fingerprint": str(row["fingerprint"] or ""),
        "started_at": int(row["started_at"] or 0),
        "state_changed_at": int(row["state_changed_at"] or 0),
        "updated_at": int(row["observed_updated_at"] or 0),
        "arrived_at": int(row["arrived_at"]) if row["arrived_at"] is not None else None,
        "canceled_at": int(row["canceled_at"]) if row["canceled_at"] is not None else None,
        "offline_since": int(row["offline_since"]) if row["offline_since"] is not None else None,
    }

def current_report(*, active_only: bool = False) -> dict[str, Any]:
    sql = "SELECT * FROM tracky_mobile_transitions"
    if active_only:
        sql += " WHERE state NOT IN ('arrived','canceled')"
    sql += " ORDER BY started_at,transition_id"
    with db() as connection:
        rows = connection.execute(sql).fetchall()
    transitions = [_row_transition(row) for row in rows]
    return {
        "available": bool(transitions),
        "protocol": MOBILE_TRANSITION_PROTOCOL,
        "schema_version": 1,
        "transitions": transitions,
        "active_count": sum(1 for item in transitions if item["state"] not in {"arrived","canceled"}),
        "identity_linking": False,
        "semantic_only": True,
        "authority_assignment": "source_site",
        "person_object_identity_linking": False,
        "temporary_context_site_authority": False,
    }

def agent_context(report: dict[str, Any] | None = None) -> dict[str, Any]:
    report = report if isinstance(report, dict) else current_report(active_only=True)
    transitions = [
        item for item in report.get("transitions", [])
        if isinstance(item, dict) and item.get("state") not in {"arrived", "canceled"}
    ]
    return {
        "protocol": MOBILE_TRANSITION_PROTOCOL,
        "active_count": len(transitions),
        "transitions": [
            {
                "transition_id": item["transition_id"],
                "subject_kind": item["subject_kind"],
                "subject_id": item["subject_id"],
                "source_site_id": item["source_site_id"],
                "destination_site_id": item["destination_site_id"],
                "state": item["state"],
                "confidence": item["confidence"],
                "temporary_context": item["temporary_context"],
                "offline_since": item["offline_since"],
                "origin_role": item["origin_role"],
                "identity_linking": False,
            }
            for item in transitions[-12:]
        ],
    }

def cloud_projection(local_site_id: str | None = None) -> dict[str, Any]:
    from . import tracky_site_topology
    report = current_report()
    topology = tracky_site_topology.current_topology()
    authority_by_site = {
        str(site.get("id") or ""): {
            "device_id": str(site.get("authority_device_id") or ""),
            "epoch": int(site.get("authority_epoch") or 0),
        }
        for site in topology.get("sites", [])
    }
    transitions = []
    for item in report["transitions"]:
        if item["origin_role"] != "local_authority":
            continue
        if local_site_id and item["source_site_id"] != local_site_id:
            continue
        authority = authority_by_site.get(item["source_site_id"]) or {}
        if not authority.get("device_id") or int(authority.get("epoch") or 0) < 1:
            continue
        enriched = dict(item)
        enriched["source_authority_device_id"] = authority["device_id"]
        enriched["source_authority_epoch"] = int(authority["epoch"])
        transitions.append(enriched)
    return {
        "protocol": MOBILE_TRANSITION_PROTOCOL,
        "schema_version": 1,
        "transitions": transitions,
        "identity_linking": False,
        "semantic_only": True,
        "summary_only": True,
        "cloud_read_only": True,
        "authority_assignment": "source_site",
        "person_object_identity_linking": False,
        "temporary_context_site_authority": False,
        "origin_scope": "local_source_site_only" if local_site_id else "unresolved_legacy_scope",
    }

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_MOBILE_TRANSITION_VERSION,
        "protocol": MOBILE_TRANSITION_PROTOCOL,
        "states": sorted(STATES),
        "stable_subjects": ["mobile_device","explicit_continuity_subject"],
        "person_object_identity_linking": False,
        "temporary_context_site_authority": False,
        "authority_assignment": "source_site",
        "cloud_read_only": True,
    }

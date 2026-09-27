from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import approvals, federated_data, local_automation, room_device_automation, vp3_os
from .remote_identity import remote_identity_metadata


TRACKY_ACTIONS_VERSION = "2.75"
TRACKY_ACTION_PROTOCOL = "physical_action.v1"
_ALLOWED_ORIGINS = {"user_request", "agent_suggestion", "automation_trigger"}
_ALLOWED_MODES = {"suggest_only", "request_approval"}
_RULE_KEY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
_EVENT_TYPE = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
_TERMINAL_ACTION_STATUSES = {"executed", "denied", "failed", "expired"}
_SAFE_EVENT_MAX_AGE_SECONDS = 300
_AUTOMATION_MIN_CONFIDENCE = 0.80


class TrackyActionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = int(status_code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TrackyActionError("Tracky action data is not valid JSON.") from exc


def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[:limit]
    if required and not text:
        raise TrackyActionError(f"{label} is required.")
    return text


def _request_id(value: Any, prefix: str = "tracky-action") -> str:
    text = _text(value, 128)
    if not text:
        text = f"{prefix}-{uuid.uuid4().hex}"
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", text):
        raise TrackyActionError("Tracky action request identity is invalid.")
    return text


def _canonical_site() -> str:
    identity = remote_identity_metadata()
    site_id = _text(identity.get("device_id"), 100, required=True, label="HomeServer device identity")
    return site_id


def _reconciliation_gate() -> dict[str, Any]:
    state = federated_data.reconciliation_state("vp3_cloud")
    if state.get("needs_reconciliation"):
        raise TrackyActionError(
            "HomeServer v2.4 reconciliation is incomplete; physical action proposals are blocked.",
            409,
        )
    return state


def _privacy_engaged() -> bool:
    manifest = vp3_os.manifest(include_hardware=False, include_device_id=False)
    privacy = manifest.get("privacy") if isinstance(manifest.get("privacy"), dict) else {}
    return bool(privacy.get("privacy_switch_engaged"))


def _event_row(event_id: str) -> dict[str, Any]:
    event_id = _text(event_id, 128, required=True, label="source_event_id")
    with db() as connection:
        row = connection.execute(
            """
            SELECT event_id,sequence_no,event_type,severity,confidence,privacy_class,occurred_at,event_json
            FROM tracky_physical_events WHERE event_id=? LIMIT 1
            """,
            (event_id,),
        ).fetchone()
    if row is None:
        raise TrackyActionError("Tracky source event was not found.", 404)
    item = dict(row)
    try:
        decoded = json.loads(str(item.pop("event_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = {}
    item["event"] = decoded if isinstance(decoded, dict) else {}
    return item


def _event_age_seconds(event: dict[str, Any]) -> int:
    raw = str(event.get("occurred_at") or "").strip()
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 10**9
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0, int((_now() - observed.astimezone(timezone.utc)).total_seconds()))


def _validate_source_event(
    event_id: str,
    *,
    origin_kind: str,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    event = _event_row(event_id)
    privacy = str(event.get("privacy_class") or "")
    if privacy not in {"cloud_derived", "user_approved"}:
        raise TrackyActionError("This Tracky event is not eligible to drive a physical action.", 403)
    confidence = float(event.get("confidence") or 0.0)
    if confidence < max(0.0, min(1.0, float(min_confidence))):
        raise TrackyActionError("Tracky event confidence is below the automation threshold.", 409)
    if origin_kind == "automation_trigger" and _event_age_seconds(event) > _SAFE_EVENT_MAX_AGE_SECONDS:
        raise TrackyActionError("Tracky event is too old to trigger a physical automation.", 409)
    if str(event.get("event_type") or "").startswith("safety."):
        event["force_suggest_only"] = True
    else:
        event["force_suggest_only"] = False
    return event


def _intent_row(intent_id: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM tracky_action_intents WHERE intent_id=? LIMIT 1",
            (intent_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["arguments"] = json.loads(item.pop("arguments_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        item["arguments"] = {}
        item.pop("arguments_json", None)
    item["permission_upgrade_required"] = bool(item.get("permission_upgrade_required"))
    return item


def _sync_intent_status(item: dict[str, Any]) -> dict[str, Any]:
    action_request_id = str(item.get("action_request_id") or "")
    if not action_request_id:
        return item
    source = str(item.get("requested_by") or "")
    with db() as connection:
        row = connection.execute(
            "SELECT status,error FROM action_requests WHERE id=? LIMIT 1",
            (action_request_id,),
        ).fetchone()
    if row is None:
        return item
    authoritative = str(row["status"] or "")
    mapped = {
        "pending": "requested",
        "executing": "requested",
        "executed": "completed",
        "denied": "denied",
        "failed": "failed",
        "expired": "expired",
    }.get(authoritative, item["status"])
    if mapped != item["status"] or (row["error"] and str(row["error"]) != str(item.get("error") or "")):
        with db() as connection:
            connection.execute(
                """
                UPDATE tracky_action_intents
                SET status=?,error=?,updated_at=CURRENT_TIMESTAMP
                WHERE intent_id=?
                """,
                (mapped, str(row["error"] or "")[:500], item["intent_id"]),
            )
        item = _intent_row(str(item["intent_id"])) or item
    item["authoritative_action_status"] = authoritative
    return item


def action_status(intent_id: str) -> dict[str, Any]:
    intent_id = _request_id(intent_id)
    item = _intent_row(intent_id)
    if item is None:
        raise TrackyActionError("Tracky action intent was not found.", 404)
    return {
        "protocol": TRACKY_ACTION_PROTOCOL,
        "intent": _sync_intent_status(item),
        "execution_authority": "existing_homeserver_action_requests",
    }


def propose_device_action(
    *,
    intent_id: str | None,
    correlation_id: str | None,
    site_id: str | None,
    origin_kind: str,
    requested_mode: str,
    device_key: str,
    command: str,
    arguments: dict[str, Any] | None,
    reason: str,
    requested_by: str,
    granted_permissions: set[str] | None = None,
    source_event_id: str = "",
) -> dict[str, Any]:
    _reconciliation_gate()
    origin = _text(origin_kind, 40, required=True, label="origin_kind").lower()
    if origin not in _ALLOWED_ORIGINS:
        raise TrackyActionError("Unsupported Tracky action origin.")
    mode = _text(requested_mode, 40, required=True, label="requested_mode").lower()
    if mode not in _ALLOWED_MODES:
        raise TrackyActionError("Unsupported Tracky action mode.")
    if origin == "automation_trigger":
        raise TrackyActionError("Remote callers cannot impersonate a local Tracky automation trigger.", 403)

    canonical_site = _canonical_site()
    routed_site = _text(site_id or canonical_site, 100)
    if routed_site != canonical_site:
        raise TrackyActionError("Tracky action is routed to a different HomeServer site.", 409)

    intent_id = _request_id(intent_id)
    correlation_id = _request_id(correlation_id or intent_id, "tracky-correlation")
    existing = _intent_row(intent_id)
    if existing is not None:
        return {"protocol": TRACKY_ACTION_PROTOCOL, "intent": _sync_intent_status(existing), "idempotent": True}

    source_event = None
    if source_event_id:
        if _privacy_engaged():
            raise TrackyActionError("Physical privacy is engaged; perception-derived action proposals are blocked.", 403)
        source_event = _validate_source_event(source_event_id, origin_kind=origin)

    try:
        validated = room_device_automation.validate_command_request(device_key, command, arguments or {})
    except room_device_automation.RoomDeviceError as exc:
        raise TrackyActionError(str(exc), exc.status_code) from exc

    device = validated["device"]
    if str(device.get("category") or "") not in room_device_automation.SAFE_CONTROL_CATEGORIES:
        raise TrackyActionError("This device category is not eligible for Tracky physical actions.", 403)

    effective_mode = mode
    if source_event and source_event.get("force_suggest_only"):
        effective_mode = "suggest_only"

    granted = set(granted_permissions or set())
    permission_upgrade_required = (
        effective_mode == "request_approval"
        and not {"devices.control", "tools.execute"}.issubset(granted)
    )
    if permission_upgrade_required:
        effective_mode = "suggest_only"

    suggestion = room_device_automation.create_suggestion(
        source_kind=f"tracky:{origin}",
        reason=_text(reason, 1000, required=True, label="reason"),
        device_key=validated["device_key"],
        command=validated["command"],
        arguments=validated["arguments"],
        source_event_type=str(source_event.get("event_type") if source_event else "tracky.user_action"),
    )

    action_request_id = ""
    status = "suggested"
    error = ""
    if effective_mode == "request_approval":
        source = f"app:{_text(requested_by, 80, required=True, label='requested_by')}"
        try:
            request = approvals.create_device_command_request(
                source,
                {
                    "device_key": validated["device_key"],
                    "command": validated["command"],
                    "arguments": validated["arguments"],
                },
                owner=False,
            )
        except approvals.ApprovalError as exc:
            raise TrackyActionError(str(exc), exc.status_code) from exc
        action_request_id = str(((request.get("result") or {}).get("request_id") or ""))
        if not action_request_id:
            raise TrackyActionError("HomeServer did not create a durable device approval request.", 500)
        room_device_automation.mark_suggestion_requested(int(suggestion["id"]), action_request_id)
        status = "requested"

    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_action_intents(
                intent_id,correlation_id,site_id,origin_kind,source_event_id,source_event_sequence,
                requested_mode,effective_mode,device_key,command,arguments_json,reason,confidence,
                status,suggestion_id,action_request_id,permission_upgrade_required,error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                intent_id, correlation_id, canonical_site, origin,
                str(source_event.get("event_id") if source_event else ""),
                int(source_event.get("sequence_no") if source_event else 0),
                mode, effective_mode, validated["device_key"], validated["command"],
                _json(validated["arguments"]), _text(reason, 1000),
                float(source_event.get("confidence") if source_event else 1.0),
                status, int(suggestion["id"]), action_request_id,
                1 if permission_upgrade_required else 0, error,
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
            VALUES ('app',?,'tracky.action.proposed','tracky_action_intent',?,?)
            """,
            (
                _text(requested_by, 80) or "vp3",
                intent_id,
                _json({
                    "origin_kind": origin,
                    "requested_mode": mode,
                    "effective_mode": effective_mode,
                    "device_key": validated["device_key"],
                    "command": validated["command"],
                    "suggestion_id": int(suggestion["id"]),
                    "action_request_id": action_request_id,
                }),
            ),
        )

    item = _intent_row(intent_id)
    return {
        "protocol": TRACKY_ACTION_PROTOCOL,
        "intent": item,
        "suggestion": suggestion,
        "approval_required": status == "requested",
        "local_owner_approval_required": status == "requested",
        "permission_upgrade_required": permission_upgrade_required,
        "remote_approval_allowed": False,
        "direct_execution": False,
    }


def upsert_event_rule(
    rule_key: str,
    name: str,
    *,
    event_type: str,
    routine_key: str,
    enabled: bool = False,
    room_id: str = "",
    subject_type: str = "",
    min_confidence: float = _AUTOMATION_MIN_CONFIDENCE,
    cooldown_seconds: int = 60,
) -> dict[str, Any]:
    key = _text(rule_key, 80, required=True, label="rule_key").lower()
    if not _RULE_KEY.fullmatch(key):
        raise TrackyActionError("Tracky automation rule_key is invalid.")
    safe_event = _text(event_type, 100, required=True, label="event_type").lower()
    if not _EVENT_TYPE.fullmatch(safe_event):
        raise TrackyActionError("Tracky automation event_type is invalid.")
    routine = local_automation.get_routine(routine_key)
    safe_room = _text(room_id, 128)
    if safe_room:
        room_device_automation.get_room(safe_room)
    safe_subject = _text(subject_type, 60).lower()
    if safe_subject and not re.fullmatch(r"[a-z0-9_.:-]{2,60}", safe_subject):
        raise TrackyActionError("Tracky automation subject_type is invalid.")
    confidence = max(_AUTOMATION_MIN_CONFIDENCE, min(1.0, float(min_confidence)))
    cooldown = max(0, min(int(cooldown_seconds), 86400))

    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_event_automation_rules(
                rule_key,name,enabled,event_type,room_id,subject_type,min_confidence,
                routine_key,cooldown_seconds
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(rule_key) DO UPDATE SET
                name=excluded.name,enabled=excluded.enabled,event_type=excluded.event_type,
                room_id=excluded.room_id,subject_type=excluded.subject_type,
                min_confidence=excluded.min_confidence,routine_key=excluded.routine_key,
                cooldown_seconds=excluded.cooldown_seconds,updated_at=CURRENT_TIMESTAMP
            """,
            (
                key, _text(name, 160, required=True, label="name"), 1 if enabled else 0,
                safe_event, safe_room, safe_subject, confidence,
                str(routine["routine_key"]), cooldown,
            ),
        )
    return get_event_rule(key)


def get_event_rule(rule_key: str) -> dict[str, Any]:
    key = _text(rule_key, 80, required=True, label="rule_key").lower()
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM tracky_event_automation_rules WHERE rule_key=? LIMIT 1",
            (key,),
        ).fetchone()
    if row is None:
        raise TrackyActionError("Tracky automation rule was not found.", 404)
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["routine"] = local_automation.get_routine(str(item["routine_key"]))
    return item


def list_event_rules() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT rule_key FROM tracky_event_automation_rules ORDER BY name,rule_key"
        ).fetchall()
    return [get_event_rule(str(row["rule_key"])) for row in rows]


def delete_event_rule(rule_key: str) -> dict[str, Any]:
    item = get_event_rule(rule_key)
    with db() as connection:
        connection.execute("DELETE FROM tracky_event_automation_rules WHERE id=?", (int(item["id"]),))
    return {"deleted": True, "rule_key": item["rule_key"]}


def _event_matches_rule(event: dict[str, Any], rule: dict[str, Any]) -> bool:
    if str(event.get("event_type") or "") != str(rule["event_type"]):
        return False
    if float(event.get("confidence") or 0.0) < float(rule["min_confidence"]):
        return False
    if str(event.get("privacy_class") or "") not in {"cloud_derived", "user_approved"}:
        return False
    if _event_age_seconds(event) > _SAFE_EVENT_MAX_AGE_SECONDS:
        return False
    raw = event.get("event") if isinstance(event.get("event"), dict) else {}
    if str(rule.get("room_id") or "") and str(raw.get("room_id") or "") != str(rule["room_id"]):
        return False
    subject = raw.get("subject") if isinstance(raw.get("subject"), dict) else {}
    if str(rule.get("subject_type") or "") and str(subject.get("type") or "") != str(rule["subject_type"]):
        return False
    return True


def _cooldown_active(rule: dict[str, Any]) -> bool:
    value = str(rule.get("last_fired_at") or "").strip()
    if not value:
        return False
    try:
        prior = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if prior.tzinfo is None:
        prior = prior.replace(tzinfo=timezone.utc)
    return (_now() - prior.astimezone(timezone.utc)).total_seconds() < int(rule["cooldown_seconds"])


def process_event_automations(event_ids: list[str]) -> list[dict[str, Any]]:
    if not event_ids or _privacy_engaged():
        return []
    _reconciliation_gate()
    settings = local_automation.get_settings()
    if not bool(settings.get("enabled")):
        return []
    rules = [rule for rule in list_event_rules() if rule["enabled"]]
    outcomes: list[dict[str, Any]] = []

    for event_id in event_ids[:100]:
        try:
            event = _validate_source_event(
                event_id,
                origin_kind="automation_trigger",
                min_confidence=_AUTOMATION_MIN_CONFIDENCE,
            )
        except TrackyActionError:
            continue
        for rule in rules:
            with db() as connection:
                claimed = connection.execute(
                    """
                    INSERT OR IGNORE INTO tracky_event_automation_claims(
                        rule_id,event_id,event_sequence,matched
                    ) VALUES (?,?,?,0)
                    """,
                    (int(rule["id"]), str(event["event_id"]), int(event["sequence_no"])),
                )
            if claimed.rowcount != 1:
                continue
            matched = _event_matches_rule(event, rule)
            if not matched:
                with db() as connection:
                    connection.execute(
                        """
                        UPDATE tracky_event_automation_claims
                        SET matched=0,result_status='not_matched',completed_at=CURRENT_TIMESTAMP
                        WHERE rule_id=? AND event_id=?
                        """,
                        (int(rule["id"]), str(event["event_id"])),
                    )
                continue
            if str(event.get("event_type") or "").startswith("safety."):
                # Safety-like observations may surface and suggest; they may not
                # autonomously promote a device command approval request.
                routine = rule["routine"]
                if str(routine.get("approval_mode") or "") != "suggest_only":
                    with db() as connection:
                        connection.execute(
                            """
                            UPDATE tracky_event_automation_claims
                            SET matched=1,result_status='blocked_safety_requires_suggest_only',
                                completed_at=CURRENT_TIMESTAMP
                            WHERE rule_id=? AND event_id=?
                            """,
                            (int(rule["id"]), str(event["event_id"])),
                        )
                    outcomes.append({
                        "rule_key": rule["rule_key"],
                        "event_id": event["event_id"],
                        "fired": False,
                        "reason": "safety_event_requires_suggest_only",
                    })
                    continue
            if _cooldown_active(rule):
                with db() as connection:
                    connection.execute(
                        """
                        UPDATE tracky_event_automation_claims
                        SET matched=1,result_status='cooldown',completed_at=CURRENT_TIMESTAMP
                        WHERE rule_id=? AND event_id=?
                        """,
                        (int(rule["id"]), str(event["event_id"])),
                    )
                outcomes.append({
                    "rule_key": rule["rule_key"],
                    "event_id": event["event_id"],
                    "fired": False,
                    "reason": "cooldown",
                })
                continue

            snapshot = {
                "source_kind": "tracky_event",
                "event_id": event["event_id"],
                "event_sequence": int(event["sequence_no"]),
                "event_type": event["event_type"],
                "confidence": float(event["confidence"]),
                "room_id": str((event.get("event") or {}).get("room_id") or ""),
            }
            try:
                result = local_automation.run_routine(
                    str(rule["routine_key"]),
                    source_kind=f"tracky-rule:{rule['rule_key']}",
                    snapshot=snapshot,
                )
                with db() as connection:
                    connection.execute(
                        "UPDATE tracky_event_automation_rules SET last_fired_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (_now_iso(), int(rule["id"])),
                    )
                    connection.execute(
                        """
                        UPDATE tracky_event_automation_claims
                        SET matched=1,execution_id=?,result_status=?,completed_at=CURRENT_TIMESTAMP
                        WHERE rule_id=? AND event_id=?
                        """,
                        (
                            int(result["execution_id"]),
                            str(result["status"]),
                            int(rule["id"]),
                            str(event["event_id"]),
                        ),
                    )
                outcomes.append({
                    "rule_key": rule["rule_key"],
                    "event_id": event["event_id"],
                    "fired": True,
                    **result,
                })
            except Exception as exc:
                with db() as connection:
                    connection.execute(
                        """
                        UPDATE tracky_event_automation_claims
                        SET matched=1,result_status='failed',error=?,completed_at=CURRENT_TIMESTAMP
                        WHERE rule_id=? AND event_id=?
                        """,
                        (f"{type(exc).__name__}: {str(exc)}"[:500], int(rule["id"]), str(event["event_id"])),
                    )
                outcomes.append({
                    "rule_key": rule["rule_key"],
                    "event_id": event["event_id"],
                    "fired": False,
                    "reason": "routine_failed",
                })
    return outcomes


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_ACTIONS_VERSION,
        "protocol": TRACKY_ACTION_PROTOCOL,
        "supported": True,
        "remote_direct_execution": False,
        "remote_approval_allowed": False,
        "default_cloud_device_control_permission": False,
        "modes": ["suggest_only", "request_approval"],
        "safe_control_categories": sorted(room_device_automation.SAFE_CONTROL_CATEGORIES),
        "blocked_control_categories": sorted(room_device_automation.BLOCKED_CONTROL_CATEGORIES),
        "event_automation": {
            "owner_defined_rules": True,
            "local_only_configuration": True,
            "uses_existing_routines": True,
            "direct_execution": False,
            "minimum_confidence": _AUTOMATION_MIN_CONFIDENCE,
            "maximum_event_age_seconds": _SAFE_EVENT_MAX_AGE_SECONDS,
            "safety_events_suggest_only": True,
        },
    }

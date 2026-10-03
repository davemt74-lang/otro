"""Shared scene meaning through canonical Tracky rows and existing HTTPS sync.

Only typed meanings leave this process. Camera and conversation authority stay local.
"""
from __future__ import annotations
import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from typing import Any
from ..database import db
from . import tracky_agent_eyes_scene as scene

CONTRACT = "tracky.agent-eyes.shared-scene.v1g3d"
KEY = "tracky_agent_eyes_semantic_share_v1g3d"
CORRECTION_KEY = "tracky_agent_eyes_owner_correction_v1g3d"
_LOCK = threading.RLock()
_PROCESS_CONSENT = False
MAX_REVISION = 2147483647
_PREFIX = "agent-eyes:"

class SharedSceneError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code

def _read(key: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT value_json FROM system_settings WHERE setting_key=?", (key,)).fetchone()
    value = json.loads(row["value_json"]) if row else {}
    if not isinstance(value, dict):
        raise SharedSceneError("Stored scene policy is invalid.", 503)
    return value

def _save(key: str, value: dict) -> None:
    with db() as connection:
        connection.execute("INSERT INTO system_settings(setting_key,value_json) VALUES(?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",
            (key, json.dumps(value, sort_keys=True, separators=(",", ":"))))

def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()

def _age(stamp: Any) -> float | None:
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - value).total_seconds()
        return age if math.isfinite(age) and age >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None

def decorate(value: dict, request_fingerprint: str) -> dict:
    correction = _read(CORRECTION_KEY)
    reports = []
    if (correction.get("request_fingerprint") == request_fingerprint
            and correction.get("room_id") == value["room_id"]):
        for label, present in sorted((correction.get("claims") or {}).items()):
            if label in scene.OBJECTS and type(present) is bool:
                reports.append({"object": label, "present": present, "source": "owner_report",
                    "conflicts_with_camera": present != (label in value["objects"])})
    from . import room_device_automation as devices
    device_context = []
    for device in devices.list_devices(room_key=value["room_id"], enabled_only=True, limit=8):
        age = _age(device.get("last_seen_at"))
        item = {"category": str(device["category"]), "source": "room_device_automation",
            "state": "recent" if age is not None and age <= 60 else "stale_or_unavailable"}
        if age is not None:
            item["age_seconds"] = round(age, 1)
        if item["state"] == "recent":
            state = device.get("state") or {}
            if type(state.get("on")) is bool:
                item["on"] = state["on"]
            if type(state.get("temperature")) in {int, float} and math.isfinite(state["temperature"]):
                item["temperature"] = max(-100, min(100, state["temperature"]))
        device_context.append(item)
    return {**value, "owner_corrections": reports, "device_context": device_context, "sources_separate": True}

def correct(*, object_label: str, present: bool, expected_fingerprint: str) -> dict:
    from . import tracky_agent_eyes_context as context
    if object_label not in scene.OBJECTS or type(present) is not bool:
        raise SharedSceneError("Select a supported object and a present/absent owner report.")
    with _LOCK:
        current = context.projection()
        observation = current.get("scene")
        if (current.get("state") != "recent_observation" or not observation
                or current.get("request_fingerprint") != expected_fingerprint):
            raise SharedSceneError("Observation changed or expired; refresh before correcting it.", 409)
        saved = _read(CORRECTION_KEY)
        claims = saved.get("claims", {}) if saved.get("request_fingerprint") == expected_fingerprint else {}
        claims = {k: v for k, v in claims.items() if k in scene.OBJECTS and type(v) is bool}
        if object_label not in claims and len(claims) >= 8:
            raise SharedSceneError("At most eight owner corrections fit this observation.")
        claims[object_label] = present
        _save(CORRECTION_KEY, {"request_fingerprint": expected_fingerprint,
            "room_id": observation["room_id"], "claims": claims})
    record_current()
    return context.projection()

def graph_rows(current: dict) -> list[dict]:
    observation = current.get("scene")
    if current.get("state") != "recent_observation" or not observation:
        return []
    source = _PREFIX + current["request_fingerprint"]
    common = {"object_id": "room:" + observation["room_id"], "confidence": 0.0,
        "temporal_state": "inferred", "source_event_id": source, "sequence_no": 0, "as_of": current["observed_at"]}
    rows = [{**common, "subject_id": "agent-eyes-object:" + label,
        "predicate": "possibly_visible_in", "value": {"state": "possible", "label": label}}
        for label in observation["objects"]]
    for report in observation.get("owner_corrections", []):
        rows.append({**common, "subject_id": "agent-eyes-owner:" + report["object"],
            "predicate": "owner_reports_present" if report["present"] else "owner_reports_absent",
            "value": {"state": "owner_report", "label": report["object"]}})
    return rows

def reserved(row: dict) -> bool:
    return (str(row.get("source_event_id") or "").startswith(_PREFIX)
        or str(row.get("subject_id") or "").startswith(("agent-eyes-object:", "agent-eyes-owner:")))

def record_current() -> None:
    from . import tracky_agent_eyes_context as context
    from . import tracky_physical_context as physical
    with _LOCK:
        rows = graph_rows(context.projection()) if _PROCESS_CONSENT else []
        with db() as connection:
            connection.execute("DELETE FROM tracky_physical_world_state WHERE source_event_id LIKE 'agent-eyes:%' OR subject_id LIKE 'agent-eyes-object:%' OR subject_id LIKE 'agent-eyes-owner:%'")
            sequence = connection.execute("SELECT COALESCE(MAX(sequence_no),0)+1 FROM tracky_physical_world_state").fetchone()[0]
            for row in rows:
                key = physical._relation_key(row)
                connection.execute("INSERT INTO tracky_physical_world_state(relation_key,subject_id,predicate,object_id,value_json,confidence,temporal_state,source_event_id,sequence_no,as_of) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(relation_key) DO UPDATE SET value_json=excluded.value_json,source_event_id=excluded.source_event_id,sequence_no=excluded.sequence_no,as_of=excluded.as_of",
                    (key, row["subject_id"], row["predicate"], row["object_id"], json.dumps(row["value"]),
                     0, "inferred", row["source_event_id"], sequence, row["as_of"]))

def local_world(rows: list[dict]) -> list[dict]:
    from . import tracky_agent_eyes_context as context
    # General Agent context may use Cloud inference. Scene meaning enters this
    # shared view only under the separate semantic-sharing consent. Local-only
    # conversations use their existing, independent per-chat opt-in instead.
    with _LOCK:
        return [r for r in rows if not reserved(r)] + (graph_rows(context.projection()) if _PROCESS_CONSENT else [])

def snapshot() -> dict | None:
    from . import tracky_agent_eyes_context as context
    with _LOCK:
        saved = _read(KEY)
        if not _PROCESS_CONSENT and not saved:
            return None
        body = {"protocol": CONTRACT, "state": "revoked", "reason": "owner_revoked"}
        if _PROCESS_CONSENT:
            current = context.projection()
            observation = current.get("scene")
            if current.get("state") == "recent_observation" and observation:
                body = {"protocol": CONTRACT, "state": "available", "reason": "recent_observation",
                    "observed_at": current["observed_at"], "room_id": observation["room_id"],
                    "objects": observation["objects"], "setting": observation["setting"], "lighting": observation["lighting"],
                    "owner_corrections": [{"object": r["object"], "present": r["present"]}
                        for r in observation.get("owner_corrections", [])]}
            else:
                body = {"protocol": CONTRACT, "state": "unavailable",
                    "reason": current.get("reason") if current.get("reason") in context.REASONS
                    and current.get("reason") != "recent_observation" else "scene_unavailable"}
        digest = fingerprint(body)
        if saved.get("fingerprint") != digest:
            previous = saved.get("revision", 0)
            if type(previous) is not int or not 0 <= previous < MAX_REVISION:
                raise SharedSceneError("Scene consent revision cannot advance.", 409)
            saved = {"revision": previous + 1, "fingerprint": digest, "summary": body,
                "acknowledged_revision": min(int(saved.get("acknowledged_revision") or 0), previous)}
            _save(KEY, saved)
        return {"revision": saved["revision"], "summary": saved["summary"], "fingerprint": saved["fingerprint"]}

def set_sharing(*, enabled: bool, consent: bool) -> dict:
    global _PROCESS_CONSENT
    if type(enabled) is not bool or consent is not True:
        raise SharedSceneError("Explicit owner consent is required for semantic sharing.", 403)
    with _LOCK:
        if enabled:
            selected = scene.binding()
            scene.check_binding(selected)
        _PROCESS_CONSENT = enabled
        snapshot()
    return status()

def pending() -> bool:
    with _LOCK:
        package = snapshot()
        if package is None:
            return False
        saved = _read(KEY)
        return saved.get("acknowledged_revision") != package["revision"]

def acknowledge(sent: dict | None, receipt: Any, *, site_id: str) -> bool:
    if sent is None or not isinstance(receipt, dict) or receipt.get("site_id") != site_id:
        return False
    with _LOCK:
        # Recheck consent and observation age after the network round trip.
        snapshot()
        saved = _read(KEY)
        current = saved.get("revision") == sent["revision"] and saved.get("fingerprint") == sent["fingerprint"]
        accepted = (receipt.get("accepted") is True and type(receipt.get("revision")) is int
            and receipt["revision"] == sent["revision"] and receipt.get("fingerprint") == sent["fingerprint"]
            and receipt.get("state") == sent["summary"]["state"])
        if not current or not accepted:
            revision = receipt.get("revision")
            if current and type(revision) is int and saved["revision"] < revision < MAX_REVISION:
                saved["revision"] = revision + 1
                _save(KEY, saved)
            return False
        saved["acknowledged_revision"] = sent["revision"]
        _save(KEY, saved)
        return True

def status() -> dict:
    with _LOCK:
        package = snapshot()
        saved = _read(KEY)
        enabled = _PROCESS_CONSENT
    return {"protocol": CONTRACT, "enabled": enabled, "fresh_process_consent_required": True,
        "revision": package["revision"] if package else 0,
        "state": package["summary"]["state"] if package else "never_shared",
        "delivery": "pending" if package and saved.get("acknowledged_revision") != package["revision"]
            else "acknowledged" if package else "not_requested",
        "scope": "filtered_scene_enums_and_owner_reports_only", "chat_history_exported": False,
        "raw_media_exported": False, "capture_authority": False}

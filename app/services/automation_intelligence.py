from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db
from . import cognitive_runtime, local_automation, room_device_automation

INTELLIGENCE_VERSION = "v0.80"
_ALLOWED_PATTERN_KINDS = {"time_action", "action_sequence"}
_ALLOWED_PROPOSAL_STATUSES = {"proposed", "materialized", "active", "dismissed", "suppressed"}
_MAX_ACTION_ROWS = 5000
_MAX_SEQUENCE_STEPS = 8

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.RLock()


class AutomationIntelligenceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _encoded(value: Any, *, max_bytes: int = 16384) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AutomationIntelligenceError(
            "Automation intelligence payload must be JSON serializable."
        ) from exc
    if len(text.encode("utf-8")) > max_bytes:
        raise AutomationIntelligenceError(
            "Automation intelligence payload exceeds the size limit.", 413
        )
    return text


def _hash(value: Any, length: int = 20) -> str:
    return hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()[:length]


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM automation_intelligence_settings WHERE id=1"
        ).fetchone()
    if row is None:
        raise AutomationIntelligenceError(
            "Automation intelligence settings are unavailable.", 500
        )
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


def update_settings(
    *,
    enabled: bool,
    scan_interval_seconds: int,
    lookback_days: int,
    min_occurrences: int,
    time_bucket_minutes: int,
    max_proposals_per_scan: int,
    suppression_days: int,
) -> dict[str, Any]:
    if scan_interval_seconds < 300 or scan_interval_seconds > 86400:
        raise AutomationIntelligenceError(
            "scan_interval_seconds must be between 300 and 86400."
        )
    if lookback_days < 7 or lookback_days > 90:
        raise AutomationIntelligenceError("lookback_days must be between 7 and 90.")
    if min_occurrences < 3 or min_occurrences > 20:
        raise AutomationIntelligenceError(
            "min_occurrences must be between 3 and 20."
        )
    if time_bucket_minutes not in {15, 30, 60, 120}:
        raise AutomationIntelligenceError(
            "time_bucket_minutes must be 15, 30, 60, or 120."
        )
    if max_proposals_per_scan < 1 or max_proposals_per_scan > 50:
        raise AutomationIntelligenceError(
            "max_proposals_per_scan must be between 1 and 50."
        )
    if suppression_days < 1 or suppression_days > 365:
        raise AutomationIntelligenceError(
            "suppression_days must be between 1 and 365."
        )
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_intelligence_settings
            SET enabled=?,scan_interval_seconds=?,lookback_days=?,min_occurrences=?,
                time_bucket_minutes=?,max_proposals_per_scan=?,suppression_days=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                1 if enabled else 0,
                int(scan_interval_seconds),
                int(lookback_days),
                int(min_occurrences),
                int(time_bucket_minutes),
                int(max_proposals_per_scan),
                int(suppression_days),
            ),
        )
    return get_settings()


def record_context_event(
    event_type: str,
    state: str,
    *,
    source_kind: str = "ambient",
    metadata: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    safe_type = str(event_type or "").strip().lower()[:80]
    safe_state = str(state or "").strip().lower()[:80]
    safe_source = str(source_kind or "system").strip().lower()[:80]
    if not safe_type or not safe_state:
        raise AutomationIntelligenceError(
            "Context event type and state are required."
        )
    safe_meta = dict(metadata or {})
    allowed_meta = {
        str(key)[:80]: value
        for key, value in safe_meta.items()
        if str(key) in {"room_key", "sensor_key", "meeting_id", "source"}
    }
    when = _parse_time(occurred_at) if occurred_at else _now()
    if when is None:
        raise AutomationIntelligenceError(
            "occurred_at must be a valid timestamp."
        )
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO automation_context_events(
                source_kind,event_type,state,metadata_json,occurred_at
            ) VALUES (?,?,?,?,?)
            """,
            (
                safe_source,
                safe_type,
                safe_state,
                _encoded(allowed_meta, max_bytes=4096),
                _iso(when),
            ),
        )
        event_id = int(cursor.lastrowid)
        cutoff = _iso(_now() - timedelta(days=90))
        connection.execute(
            "DELETE FROM automation_context_events WHERE occurred_at<?",
            (cutoff,),
        )
    return {
        "id": event_id,
        "source_kind": safe_source,
        "event_type": safe_type,
        "state": safe_state,
        "metadata": allowed_meta,
        "occurred_at": _iso(when),
    }


def list_context_events(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT * FROM automation_context_events
            ORDER BY occurred_at DESC,id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _decode(item.pop("metadata_json", "{}"), {})
        output.append(item)
    return output


def _arguments_from_meta(command: str, meta: dict[str, Any]) -> dict[str, Any]:
    if command == "set_brightness" and "brightness" in meta:
        return {"brightness": int(meta["brightness"])}
    if command == "set_temperature" and "temperature_f" in meta:
        return {"temperature_f": float(meta["temperature_f"])}
    if command == "set_mode" and "mode" in meta:
        return {"mode": str(meta["mode"])[:20]}
    return {}


def _load_actions(settings: dict[str, Any]) -> list[dict[str, Any]]:
    cutoff = _now() - timedelta(days=int(settings["lookback_days"]))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT a.id,a.source_app_key,a.command,a.arguments_meta_json,a.created_at,
                   d.device_key,d.name AS device_name,d.category,
                   r.room_key,r.name AS room_name
            FROM automation_device_actions a
            JOIN automation_devices d ON d.id=a.device_id
            LEFT JOIN automation_rooms r ON r.id=d.room_id
            WHERE a.status='completed'
              AND a.source_app_key NOT LIKE 'automation:%'
            ORDER BY a.created_at ASC,a.id ASC
            LIMIT ?
            """,
            (_MAX_ACTION_ROWS,),
        ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        when = _parse_time(row["created_at"])
        if when is None or when < cutoff:
            continue
        meta = _decode(row["arguments_meta_json"], {})
        action = {
            "id": int(row["id"]),
            "source_app_key": str(row["source_app_key"]),
            "device_key": str(row["device_key"]),
            "device_name": str(row["device_name"]),
            "category": str(row["category"]),
            "room_key": row["room_key"],
            "room_name": row["room_name"],
            "command": str(row["command"]),
            "arguments": _arguments_from_meta(str(row["command"]), meta),
            "occurred_at": _iso(when),
            "_when": when,
        }
        try:
            room_device_automation.validate_command_request(
                action["device_key"],
                action["command"],
                action["arguments"],
            )
        except room_device_automation.RoomDeviceError:
            continue
        output.append(action)
    return output


def _bucket_index(when: datetime, minutes: int) -> int:
    return (when.hour * 60 + when.minute) // minutes


def _bucket_clock(bucket: int, minutes: int) -> tuple[int, int]:
    total = int(bucket) * int(minutes)
    return (total // 60) % 24, total % 60


def _step(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_key": action["device_key"],
        "command": action["command"],
        "arguments": action["arguments"],
    }


def _confidence(count: int, minimum: int, *, sequence: bool = False) -> float:
    base = 0.58 if sequence else 0.54
    surplus = max(0, int(count) - int(minimum))
    return round(
        min(
            0.95,
            base
            + min(0.24, minimum * 0.045)
            + min(0.13, surplus * 0.025),
        ),
        3,
    )


def _context_near(times: list[datetime]) -> dict[str, Any]:
    if not times:
        return {}
    start = min(times) - timedelta(minutes=45)
    end = max(times) + timedelta(minutes=45)
    with db() as connection:
        rows = connection.execute(
            """
            SELECT event_type,state,COUNT(*) AS occurrences
            FROM automation_context_events
            WHERE occurred_at>=? AND occurred_at<=?
            GROUP BY event_type,state
            ORDER BY occurrences DESC,event_type,state
            LIMIT 8
            """,
            (_iso(start), _iso(end)),
        ).fetchall()
    return {
        "nearby_context": [
            {
                "event_type": row["event_type"],
                "state": row["state"],
                "occurrences": int(row["occurrences"]),
            }
            for row in rows
        ]
    }


def _candidate_patterns(
    actions: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    minutes = int(settings["time_bucket_minutes"])
    minimum = int(settings["min_occurrences"])
    singles: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    sessions: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)

    for action in actions:
        signature = _encoded(_step(action), max_bytes=4096)
        bucket = _bucket_index(action["_when"], minutes)
        singles[(signature, bucket)].append(action)
        sessions[(action["_when"].date().isoformat(), bucket)].append(action)

    candidates: list[dict[str, Any]] = []
    for (signature_text, bucket), evidence in singles.items():
        if len(evidence) < minimum:
            continue
        step = json.loads(signature_text)
        times = [item["_when"] for item in evidence]
        candidates.append(
            {
                "pattern_kind": "time_action",
                "signature": [step],
                "time_bucket": bucket,
                "weekdays": sorted(
                    {item["_when"].weekday() for item in evidence}
                ),
                "evidence_count": len(evidence),
                "confidence": _confidence(len(evidence), minimum),
                "first_seen_at": _iso(min(times)),
                "last_seen_at": _iso(max(times)),
                "evidence": {
                    "action_ids": [item["id"] for item in evidence[-20:]],
                    "source_count": len(
                        {item["source_app_key"] for item in evidence}
                    ),
                    **_context_near(times),
                },
                "device_names": [evidence[-1]["device_name"]],
                "room_names": sorted(
                    {
                        str(item["room_name"])
                        for item in evidence
                        if item.get("room_name")
                    }
                ),
            }
        )

    sequences: dict[
        tuple[str, int], list[list[dict[str, Any]]]
    ] = defaultdict(list)
    for (_, bucket), session in sessions.items():
        ordered = sorted(session, key=lambda item: (item["_when"], item["id"]))
        compact: list[dict[str, Any]] = []
        seen_steps: set[str] = set()
        for action in ordered:
            step = _step(action)
            sig = _encoded(step, max_bytes=4096)
            if sig in seen_steps:
                continue
            seen_steps.add(sig)
            compact.append(action)
            if len(compact) >= _MAX_SEQUENCE_STEPS:
                break
        if len(compact) < 2:
            continue
        sequence_signature = [_step(item) for item in compact]
        sequences[(_encoded(sequence_signature), bucket)].append(compact)

    for (signature_text, bucket), occurrences in sequences.items():
        if len(occurrences) < minimum:
            continue
        signature = json.loads(signature_text)
        flat = [item for occurrence in occurrences for item in occurrence]
        times = [item["_when"] for item in flat]
        candidates.append(
            {
                "pattern_kind": "action_sequence",
                "signature": signature,
                "time_bucket": bucket,
                "weekdays": sorted(
                    {
                        occurrence[0]["_when"].weekday()
                        for occurrence in occurrences
                    }
                ),
                "evidence_count": len(occurrences),
                "confidence": _confidence(
                    len(occurrences), minimum, sequence=True
                ),
                "first_seen_at": _iso(min(times)),
                "last_seen_at": _iso(max(times)),
                "evidence": {
                    "action_ids": [item["id"] for item in flat[-40:]],
                    "sequence_steps": len(signature),
                    "source_count": len(
                        {item["source_app_key"] for item in flat}
                    ),
                    **_context_near(times),
                },
                "device_names": [
                    item["device_name"] for item in occurrences[-1]
                ],
                "room_names": sorted(
                    {
                        str(item["room_name"])
                        for item in flat
                        if item.get("room_name")
                    }
                ),
            }
        )

    candidates.sort(
        key=lambda item: (
            item["confidence"],
            item["evidence_count"],
            len(item["signature"]),
        ),
        reverse=True,
    )
    return candidates


def _proposal_title(candidate: dict[str, Any]) -> str:
    names = []
    for name in candidate.get("device_names") or []:
        safe = str(name)
        if safe not in names:
            names.append(safe)
    if candidate["pattern_kind"] == "action_sequence":
        if candidate.get("room_names"):
            return f"Routine for {candidate['room_names'][0]}"
        return "Repeated device routine"
    action = candidate["signature"][0]
    device = names[0] if names else str(action["device_key"])
    command = str(action["command"]).replace("_", " ")
    return f"{device}: {command}"


def _drafts(
    candidate: dict[str, Any],
    pattern_key: str,
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    hour, minute = _bucket_clock(
        int(candidate["time_bucket"]),
        int(settings["time_bucket_minutes"]),
    )
    token = pattern_key.split("-", 1)[-1][:12]
    title = _proposal_title(candidate)
    routine_key = f"learned-{token}"
    rule_key = f"learned-{token}"
    routine = {
        "routine_key": routine_key,
        "name": title[:160],
        "description": (
            "Drafted from repeated local device-action evidence "
            "by VP3 OS v0.80."
        ),
        "enabled": False,
        "approval_mode": "ask_every_time",
        "steps": candidate["signature"],
    }
    rule = {
        "rule_key": rule_key,
        "name": f"Learned: {title}"[:160],
        "description": (
            "Disabled draft. Owner review and explicit enable are required."
        ),
        "enabled": False,
        "routine_key": routine_key,
        "trigger_kind": "daily",
        "trigger": {
            "hour": hour,
            "minute": minute,
            "weekdays": candidate["weekdays"]
            or [0, 1, 2, 3, 4, 5, 6],
            "timezone": "UTC",
        },
        "conditions": [],
        "cooldown_seconds": max(
            3600, int(settings["time_bucket_minutes"]) * 60
        ),
    }
    simulation = {
        "lookback_days": int(settings["lookback_days"]),
        "historical_occurrences": int(candidate["evidence_count"]),
        "would_have_triggered": int(candidate["evidence_count"]),
        "steps_per_trigger": len(candidate["signature"]),
        "approval_requests_if_enabled": (
            int(candidate["evidence_count"]) * len(candidate["signature"])
        ),
        "physical_actions_without_owner_approval": 0,
    }
    return routine, rule, simulation


def _proposal_suppressed(
    existing: dict[str, Any] | None,
    now: datetime,
) -> bool:
    if not existing:
        return False
    if existing.get("status") not in {"dismissed", "suppressed"}:
        return False
    until = _parse_time(existing.get("suppression_until"))
    return bool(until and until > now)


def _emit_awareness(proposal: dict[str, Any]) -> None:
    try:
        cognitive_runtime.emit_event(
            source_app_key="vp3-os:automation-intelligence",
            event_id=(
                f"automation-proposal-{proposal['proposal_key']}-"
                f"{proposal['occurrence_count']}"
            ),
            event_type="automation.opportunity",
            summary=(
                f"Automation opportunity: {proposal['title']}. "
                f"Observed {proposal['occurrence_count']} times; "
                "owner review is required."
            ),
            source_kind="system",
            entity_type="automation_proposal",
            entity_key=str(proposal["id"]),
            importance=min(
                0.85, max(0.4, float(proposal["confidence"]))
            ),
            privacy_scope="private",
            payload={
                "proposal_id": int(proposal["id"]),
                "confidence": float(proposal["confidence"]),
                "occurrence_count": int(proposal["occurrence_count"]),
                "status": proposal["status"],
            },
            memory_candidate=False,
        )
    except Exception:
        return


def _upsert_candidate(
    candidate: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    now = _now()
    pattern_identity = {
        "kind": candidate["pattern_kind"],
        "signature": candidate["signature"],
        "bucket": candidate["time_bucket"],
    }
    pattern_key = f"pattern-{_hash(pattern_identity)}"
    proposal_key = f"proposal-{_hash(pattern_identity)}"
    routine, rule, simulation = _drafts(
        candidate, pattern_key, settings
    )
    title = _proposal_title(candidate)
    rationale = (
        f"VP3 observed this {candidate['evidence_count']} times within "
        f"the last {settings['lookback_days']} days around "
        f"{rule['trigger']['hour']:02d}:{rule['trigger']['minute']:02d} UTC. "
        "This is a suggestion only; creating the draft does not enable it."
    )
    with db() as connection:
        existing_row = connection.execute(
            """
            SELECT * FROM automation_proposals
            WHERE proposal_key=? LIMIT 1
            """,
            (proposal_key,),
        ).fetchone()
        existing = dict(existing_row) if existing_row else None
        if _proposal_suppressed(existing, now):
            return None

        connection.execute(
            """
            INSERT INTO automation_learning_patterns(
                pattern_key,pattern_kind,signature_json,time_bucket,
                weekdays_json,evidence_count,confidence,first_seen_at,
                last_seen_at,evidence_json,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,'active')
            ON CONFLICT(pattern_key) DO UPDATE SET
                weekdays_json=excluded.weekdays_json,
                evidence_count=excluded.evidence_count,
                confidence=excluded.confidence,
                first_seen_at=excluded.first_seen_at,
                last_seen_at=excluded.last_seen_at,
                evidence_json=excluded.evidence_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                pattern_key,
                candidate["pattern_kind"],
                _encoded(candidate["signature"]),
                int(candidate["time_bucket"]),
                _encoded(candidate["weekdays"]),
                int(candidate["evidence_count"]),
                float(candidate["confidence"]),
                candidate["first_seen_at"],
                candidate["last_seen_at"],
                _encoded(candidate["evidence"]),
            ),
        )
        pattern_id = int(
            connection.execute(
                """
                SELECT id FROM automation_learning_patterns
                WHERE pattern_key=?
                """,
                (pattern_key,),
            ).fetchone()["id"]
        )

        if existing and existing["status"] in {"materialized", "active"}:
            connection.execute(
                """
                UPDATE automation_proposals
                SET confidence=?,occurrence_count=?,simulation_json=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE proposal_key=?
                """,
                (
                    float(candidate["confidence"]),
                    int(candidate["evidence_count"]),
                    _encoded(simulation),
                    proposal_key,
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO automation_proposals(
                    proposal_key,pattern_id,title,rationale,
                    draft_routine_json,draft_rule_json,simulation_json,
                    status,confidence,occurrence_count,suppression_until,
                    last_suggested_at
                ) VALUES (?,?,?,?,?,?,?,'proposed',?,?,NULL,CURRENT_TIMESTAMP)
                ON CONFLICT(proposal_key) DO UPDATE SET
                    pattern_id=excluded.pattern_id,
                    title=excluded.title,
                    rationale=excluded.rationale,
                    draft_routine_json=excluded.draft_routine_json,
                    draft_rule_json=excluded.draft_rule_json,
                    simulation_json=excluded.simulation_json,
                    status='proposed',
                    confidence=excluded.confidence,
                    occurrence_count=excluded.occurrence_count,
                    suppression_until=NULL,
                    last_suggested_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    proposal_key,
                    pattern_id,
                    title,
                    rationale,
                    _encoded(routine),
                    _encoded(rule),
                    _encoded(simulation),
                    float(candidate["confidence"]),
                    int(candidate["evidence_count"]),
                ),
            )
        proposal_id = int(
            connection.execute(
                """
                SELECT id FROM automation_proposals
                WHERE proposal_key=?
                """,
                (proposal_key,),
            ).fetchone()["id"]
        )
    proposal = get_proposal(proposal_id)
    if proposal["status"] == "proposed":
        _emit_awareness(proposal)
    return proposal


def scan_patterns() -> dict[str, Any]:
    settings = get_settings()
    if not settings["enabled"]:
        return {
            "enabled": False,
            "actions_considered": 0,
            "patterns_found": 0,
            "proposals": [],
        }
    actions = _load_actions(settings)
    candidates = _candidate_patterns(actions, settings)
    proposals: list[dict[str, Any]] = []
    for candidate in candidates:
        proposal = _upsert_candidate(candidate, settings)
        if proposal is not None:
            proposals.append(proposal)
        if len(proposals) >= int(settings["max_proposals_per_scan"]):
            break
    return {
        "enabled": True,
        "actions_considered": len(actions),
        "patterns_found": len(candidates),
        "proposals": proposals,
    }


def get_pattern(pattern_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT * FROM automation_learning_patterns
            WHERE id=? LIMIT 1
            """,
            (int(pattern_id),),
        ).fetchone()
    if row is None:
        raise AutomationIntelligenceError(
            "Learning pattern not found.", 404
        )
    item = dict(row)
    item["signature"] = _decode(
        item.pop("signature_json", "[]"), []
    )
    item["weekdays"] = _decode(
        item.pop("weekdays_json", "[]"), []
    )
    item["evidence"] = _decode(
        item.pop("evidence_json", "{}"), {}
    )
    return item


def list_patterns(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id FROM automation_learning_patterns
            ORDER BY confidence DESC,evidence_count DESC,id DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return [get_pattern(int(row["id"])) for row in rows]


def get_proposal(proposal_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT p.*,lp.pattern_key,lp.pattern_kind,lp.evidence_json
            FROM automation_proposals p
            JOIN automation_learning_patterns lp ON lp.id=p.pattern_id
            WHERE p.id=? LIMIT 1
            """,
            (int(proposal_id),),
        ).fetchone()
    if row is None:
        raise AutomationIntelligenceError(
            "Automation proposal not found.", 404
        )
    item = dict(row)
    item["draft_routine"] = _decode(
        item.pop("draft_routine_json", "{}"), {}
    )
    item["draft_rule"] = _decode(
        item.pop("draft_rule_json", "{}"), {}
    )
    item["simulation"] = _decode(
        item.pop("simulation_json", "{}"), {}
    )
    item["evidence"] = _decode(
        item.pop("evidence_json", "{}"), {}
    )
    return item


def list_proposals(
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    params: list[Any] = []
    where = ""
    if status:
        safe_status = str(status).strip().lower()
        if safe_status not in _ALLOWED_PROPOSAL_STATUSES:
            raise AutomationIntelligenceError(
                "Invalid proposal status."
            )
        where = "WHERE status=?"
        params.append(safe_status)
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT id FROM automation_proposals {where}
            ORDER BY confidence DESC,occurrence_count DESC,id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [get_proposal(int(row["id"])) for row in rows]


def _feedback(
    proposal_id: int,
    decision: str,
    note: str = "",
) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO automation_proposal_feedback(
                proposal_id,decision,note
            ) VALUES (?,?,?)
            """,
            (
                int(proposal_id),
                decision,
                str(note or "")[:1000] or None,
            ),
        )


def dismiss_proposal(
    proposal_id: int,
    *,
    note: str = "",
) -> dict[str, Any]:
    proposal = get_proposal(proposal_id)
    if proposal["status"] not in {
        "proposed",
        "dismissed",
        "suppressed",
    }:
        raise AutomationIntelligenceError(
            "Only an unmaterialized proposal can be dismissed.", 409
        )
    days = int(get_settings()["suppression_days"])
    until = _iso(_now() + timedelta(days=days))
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_proposals
            SET status='suppressed',suppression_until=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (until, int(proposal_id)),
        )
        connection.execute(
            """
            UPDATE automation_learning_patterns
            SET status='suppressed',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(proposal["pattern_id"]),),
        )
    _feedback(proposal_id, "suppressed", note)
    return get_proposal(proposal_id)


def materialize_proposal(proposal_id: int) -> dict[str, Any]:
    proposal = get_proposal(proposal_id)
    if proposal["status"] != "proposed":
        raise AutomationIntelligenceError(
            "Only a proposed automation can be materialized.", 409
        )
    routine = proposal["draft_routine"]
    rule = proposal["draft_rule"]

    created_routine = local_automation.upsert_routine(
        routine["routine_key"],
        routine["name"],
        description=routine.get("description", ""),
        enabled=False,
        approval_mode="ask_every_time",
        steps=routine["steps"],
    )
    created_rule = local_automation.upsert_rule(
        rule["rule_key"],
        rule["name"],
        routine_key=created_routine["routine_key"],
        trigger_kind=rule["trigger_kind"],
        trigger=rule["trigger"],
        conditions=rule.get("conditions", []),
        description=rule.get("description", ""),
        enabled=False,
        cooldown_seconds=int(
            rule.get("cooldown_seconds", 3600)
        ),
    )
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_proposals
            SET status='materialized',
                materialized_routine_key=?,
                materialized_rule_key=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                created_routine["routine_key"],
                created_rule["rule_key"],
                int(proposal_id),
            ),
        )
        connection.execute(
            """
            UPDATE automation_learning_patterns
            SET status='materialized',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(proposal["pattern_id"]),),
        )
    _feedback(proposal_id, "materialized")
    return get_proposal(proposal_id)


def enable_materialized_proposal(
    proposal_id: int,
) -> dict[str, Any]:
    proposal = get_proposal(proposal_id)
    if proposal["status"] != "materialized":
        raise AutomationIntelligenceError(
            "Proposal must be materialized before it can be enabled.",
            409,
        )
    routine_key = str(
        proposal.get("materialized_routine_key") or ""
    )
    rule_key = str(proposal.get("materialized_rule_key") or "")
    if not routine_key or not rule_key:
        raise AutomationIntelligenceError(
            "Materialized proposal is missing its v0.70 objects.",
            409,
        )
    local_automation.set_routine_enabled(routine_key, True)
    local_automation.set_rule_enabled(rule_key, True)
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_proposals
            SET status='active',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(proposal_id),),
        )
    _feedback(proposal_id, "enabled")
    return get_proposal(proposal_id)


def simulate_proposal(proposal_id: int) -> dict[str, Any]:
    proposal = get_proposal(proposal_id)
    simulation = dict(proposal["simulation"])
    settings = get_settings()
    end = _now()
    start = end - timedelta(days=int(settings["lookback_days"]))
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO automation_simulations(
                proposal_id,window_start,window_end,result_json
            ) VALUES (?,?,?,?)
            """,
            (
                int(proposal_id),
                _iso(start),
                _iso(end),
                _encoded(simulation),
            ),
        )
        simulation_id = int(cursor.lastrowid)
    return {"simulation_id": simulation_id, **simulation}


def list_simulations(
    proposal_id: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    get_proposal(proposal_id)
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,window_start,window_end,result_json,created_at
            FROM automation_simulations
            WHERE proposal_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (int(proposal_id), bounded),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "result": _decode(row["result_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def overview() -> dict[str, Any]:
    with db() as connection:
        pattern_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM automation_learning_patterns"
            ).fetchone()[0]
        )
        proposed_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM automation_proposals
                WHERE status='proposed'
                """
            ).fetchone()[0]
        )
        materialized_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM automation_proposals
                WHERE status='materialized'
                """
            ).fetchone()[0]
        )
        active_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM automation_proposals
                WHERE status='active'
                """
            ).fetchone()[0]
        )
    return {
        "version": INTELLIGENCE_VERSION,
        "settings": get_settings(),
        "counts": {
            "patterns": pattern_count,
            "proposed": proposed_count,
            "materialized": materialized_count,
            "active": active_count,
        },
        "patterns": list_patterns(50),
        "proposals": list_proposals(None, 50),
        "recent_context": list_context_events(50),
    }


def _worker() -> None:
    while not _STOP.is_set():
        wait = 3600
        try:
            settings = get_settings()
            wait = max(
                300, int(settings["scan_interval_seconds"])
            )
            if settings["enabled"]:
                scan_patterns()
        except Exception:
            wait = max(300, wait)
        _STOP.wait(wait)


def start() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_worker,
            name="vp3-automation-intelligence",
            daemon=True,
        )
        _THREAD.start()


def stop() -> None:
    global _THREAD
    _STOP.set()
    with _LOCK:
        thread = _THREAD
        _THREAD = None
    if thread and thread.is_alive():
        thread.join(timeout=2.0)


def public_capability() -> dict[str, Any]:
    return {
        "version": INTELLIGENCE_VERSION,
        "local_learning": True,
        "learning_sources": [
            "completed_device_actions",
            "bounded_presence_context",
        ],
        "pattern_types": sorted(_ALLOWED_PATTERN_KINDS),
        "cognitive_awareness": True,
        "creates_disabled_drafts_only": True,
        "explicit_owner_enable_required": True,
        "direct_physical_execution": False,
        "creates_action_requests": False,
        "approves_action_requests": False,
        "self_learning_feedback_loops": False,
    }

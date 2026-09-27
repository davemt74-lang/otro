from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..database import db

MODEL_LIFECYCLE_PROTOCOL = "physical_model_lifecycle.v1"
MODEL_LIFECYCLE_VERSION = "2.77"
_ALLOWED_CHANNELS = {"shadow", "canary", "active", "retired", "rolled_back"}
_ALLOWED_MODEL_STATUS = {"evaluating", "canary", "active", "known_good", "rolled_back", "degraded"}
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|"
    r"embedding|embeddings|face_embedding|transcript|filesystem_path|file_path|"
    r"source_uri|camera_uri|pixels|bytes|blob)(?:$|_)",
    re.IGNORECASE,
)


class TrackyLifecycleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = int(status_code)


def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    result = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not result:
        raise TrackyLifecycleError(f"{label} is required.")
    return result


def _metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _assert_semantic(value: Any, path: str = "model_lifecycle", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyLifecycleError("Tracky model lifecycle nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 128:
            raise TrackyLifecycleError("Tracky model lifecycle object is too large.")
        for key, child in value.items():
            name = str(key)
            if _FORBIDDEN_KEY.search(name):
                raise TrackyLifecycleError(
                    f"Tracky model lifecycle contains local-only perception data at {path}.{name}."
                )
            _assert_semantic(child, f"{path}.{name}", depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise TrackyLifecycleError("Tracky model lifecycle list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, str) and len(value) > 20000:
        raise TrackyLifecycleError("Tracky model lifecycle contains an oversized value.")


def _timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _json(value: dict[str, Any]) -> str:
    _assert_semantic(value)
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TrackyLifecycleError("Tracky model lifecycle report is not valid JSON.") from exc


def _normalize_model(value: dict[str, Any], expected_channel: str) -> dict[str, Any]:
    channel = _text(value.get("channel") or expected_channel, 20).lower()
    if channel not in _ALLOWED_CHANNELS or channel != expected_channel:
        raise TrackyLifecycleError("Tracky model lifecycle channel is invalid.")
    status = _text(value.get("status") or "evaluating", 32).lower()
    if status not in _ALLOWED_MODEL_STATUS:
        raise TrackyLifecycleError("Tracky model lifecycle status is invalid.")
    percent = max(0.0, min(100.0, float(value.get("canaryPercent") or 0)))
    return {
        "id": _text(value.get("id"), 180, required=True, label="model id"),
        "modelKey": _text(value.get("modelKey"), 128, required=True, label="modelKey"),
        "modelVersion": _text(value.get("modelVersion"), 80, required=True, label="modelVersion"),
        "channel": channel,
        "status": status,
        "registeredAt": _timestamp(value.get("registeredAt")),
        "activatedAt": _timestamp(value.get("activatedAt")),
        "retiredAt": _timestamp(value.get("retiredAt")),
        "knownGood": bool(value.get("knownGood")),
        "canaryPercent": percent,
        "previousActiveId": _text(value.get("previousActiveId"), 180) or None,
        "packageChecksum": _text(value.get("packageChecksum"), 128) or None,
        "runtime": _text(value.get("runtime") or "tracky", 80),
        "metadata": value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
    }


def _normalize_environment_profile(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "profileKey": _text(value.get("profileKey"), 128, required=True, label="profileKey"),
        "roomId": _text(value.get("roomId"), 128) or None,
        "cameraId": _text(value.get("cameraId"), 128) or None,
        "angleKey": _text(value.get("angleKey") or "default", 80),
        "lightingBucket": _text(value.get("lightingBucket") or "unknown", 60),
        "layoutFingerprint": _text(value.get("layoutFingerprint"), 128) or None,
        "timeBucket": _text(value.get("timeBucket") or "any", 40),
        "seasonBucket": _text(value.get("seasonBucket") or "any", 40),
        "modelKey": _text(value.get("modelKey"), 128, required=True, label="modelKey"),
        "modelVersion": _text(value.get("modelVersion"), 80, required=True, label="modelVersion"),
        "calibrationKey": _text(value.get("calibrationKey"), 128),
        "metadata": value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
        "updatedAt": _timestamp(value.get("updatedAt")),
    }


def _normalize_decision(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(value.get("id"), 128, required=True, label="decision id"),
        "type": _text(value.get("type"), 60, required=True, label="decision type"),
        "modelId": _text(value.get("modelId"), 180, required=True, label="decision model"),
        "fromChannel": _text(value.get("fromChannel"), 30),
        "toChannel": _text(value.get("toChannel"), 30),
        "reason": _text(value.get("reason"), 500),
        "automatic": bool(value.get("automatic")),
        "metrics": value.get("metrics") if isinstance(value.get("metrics"), dict) else {},
        "at": _timestamp(value.get("at")),
    }


def _normalize_scenario(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"runs": 0, "passRate": None, "criticalFailures": 0, "passed": False}
    return {
        "runs": max(0, min(int(value.get("runs") or 0), 1_000_000)),
        "passRate": _metric(value.get("passRate")),
        "criticalFailures": max(0, min(int(value.get("criticalFailures") or 0), 1_000_000)),
        "passed": bool(value.get("passed")),
    }


def normalize_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrackyLifecycleError("Tracky model lifecycle report must be an object.")
    _assert_semantic(value)
    if str(value.get("protocol") or MODEL_LIFECYCLE_PROTOCOL) != MODEL_LIFECYCLE_PROTOCOL:
        raise TrackyLifecycleError("Tracky model lifecycle protocol is unsupported.")
    schema_version = max(1, min(int(value.get("schemaVersion") or 1), 100))

    normalized: dict[str, Any] = {
        "schemaVersion": schema_version,
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
    }
    for key, channel in (
        ("active", "active"),
        ("shadow", "shadow"),
        ("canary", "canary"),
        ("degraded", "active"),
    ):
        rows = value.get(key) if isinstance(value.get(key), list) else []
        if len(rows) > 64:
            raise TrackyLifecycleError("Tracky model lifecycle model list is too large.")
        normalized[key] = [
            _normalize_model(item, channel)
            for item in rows
            if isinstance(item, dict)
        ]

    profiles = value.get("environmentProfiles") if isinstance(value.get("environmentProfiles"), list) else []
    if len(profiles) > 256:
        raise TrackyLifecycleError("Tracky model lifecycle environment profile list is too large.")
    normalized["environmentProfiles"] = [
        _normalize_environment_profile(item) for item in profiles if isinstance(item, dict)
    ]

    decisions = value.get("recentDecisions") if isinstance(value.get("recentDecisions"), list) else []
    normalized["recentDecisions"] = [
        _normalize_decision(item) for item in decisions[-50:] if isinstance(item, dict)
    ]

    raw_scenarios = value.get("scenarioStatus") if isinstance(value.get("scenarioStatus"), dict) else {}
    normalized["scenarioStatus"] = {
        _text(model_id, 180): _normalize_scenario(summary)
        for model_id, summary in list(raw_scenarios.items())[:128]
        if _text(model_id, 180)
    }
    normalized["boundaries"] = [
        _text(item, 120)
        for item in (value.get("boundaries") if isinstance(value.get("boundaries"), list) else [])
        if _text(item, 120)
    ][:24]
    return normalized


def _write_report(connection, report: dict[str, Any], observed_at: str, source: str) -> dict[str, Any]:
    encoded = _json(report)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    prior = connection.execute(
        "SELECT fingerprint FROM tracky_model_lifecycle WHERE id=1"
    ).fetchone()
    changed = prior is None or str(prior["fingerprint"] or "") != fingerprint
    connection.execute(
        """
        INSERT INTO tracky_model_lifecycle(
            id,protocol,schema_version,report_json,fingerprint,generated_at,observed_at,source,updated_at
        ) VALUES (1,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            protocol=excluded.protocol,
            schema_version=excluded.schema_version,
            report_json=excluded.report_json,
            fingerprint=excluded.fingerprint,
            generated_at=excluded.generated_at,
            observed_at=excluded.observed_at,
            source=excluded.source,
            updated_at=CASE
                WHEN tracky_model_lifecycle.fingerprint<>excluded.fingerprint
                THEN CURRENT_TIMESTAMP ELSE tracky_model_lifecycle.updated_at END
        """,
        (
            MODEL_LIFECYCLE_PROTOCOL,
            int(report["schemaVersion"]),
            encoded,
            fingerprint,
            observed_at,
            observed_at,
            source,
        ),
    )
    return {"changed": changed, "fingerprint": fingerprint, "report": report}


def ingest_report(
    value: Any,
    *,
    observed_at: str | None = None,
    source: str = "tracky",
    connection=None,
) -> dict[str, Any]:
    report = normalize_report(value)
    observed = _timestamp(observed_at) or datetime.now(timezone.utc).isoformat()
    safe_source = _text(source or "tracky", 80)
    if connection is not None:
        return _write_report(connection, report, observed, safe_source)
    with db() as local:
        return _write_report(local, report, observed, safe_source)


def current_report() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT protocol,schema_version,report_json,fingerprint,generated_at,observed_at,source,updated_at
            FROM tracky_model_lifecycle WHERE id=1
            """
        ).fetchone()
    if row is None:
        return {
            "protocol": MODEL_LIFECYCLE_PROTOCOL,
            "available": False,
            "authority": "local_tracky",
            "report": {},
        }
    try:
        report = json.loads(str(row["report_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        report = {}
    return {
        "protocol": str(row["protocol"] or MODEL_LIFECYCLE_PROTOCOL),
        "available": bool(report),
        "authority": "local_tracky",
        "fingerprint": str(row["fingerprint"] or ""),
        "generated_at": row["generated_at"],
        "observed_at": row["observed_at"],
        "updated_at": row["updated_at"],
        "source": str(row["source"] or "tracky"),
        "report": report if isinstance(report, dict) else {},
    }


def health_summary() -> dict[str, Any]:
    current = current_report()
    report = current.get("report") if isinstance(current.get("report"), dict) else {}
    active = report.get("active") if isinstance(report.get("active"), list) else []
    shadow = report.get("shadow") if isinstance(report.get("shadow"), list) else []
    canary = report.get("canary") if isinstance(report.get("canary"), list) else []
    degraded = report.get("degraded") if isinstance(report.get("degraded"), list) else []
    decisions = report.get("recentDecisions") if isinstance(report.get("recentDecisions"), list) else []
    recent_rollbacks = [
        item for item in decisions[-20:]
        if isinstance(item, dict) and str(item.get("type") or "") == "rollback"
    ]
    if degraded:
        state = "degraded"
    elif recent_rollbacks:
        state = "recovering"
    elif active:
        state = "healthy"
    elif canary or shadow:
        state = "evaluating"
    else:
        state = "empty"
    return {
        "state": state,
        "active_models": len(active),
        "shadow_models": len(shadow),
        "canary_models": len(canary),
        "degraded_models": len(degraded),
        "environment_profiles": len(
            report.get("environmentProfiles")
            if isinstance(report.get("environmentProfiles"), list)
            else []
        ),
        "recent_rollbacks": len(recent_rollbacks),
        "observed_at": current.get("observed_at"),
        "authority": "local_tracky",
        "activation_authority": "local_only",
        "rollback_authority": "local_only",
        "cloud_read_only": True,
    }


def _compact_model(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(value.get("id"), 180),
        "model_key": _text(value.get("modelKey"), 128),
        "model_version": _text(value.get("modelVersion"), 80),
        "status": _text(value.get("status"), 32),
        "known_good": bool(value.get("knownGood")),
        "canary_percent": max(0.0, min(100.0, float(value.get("canaryPercent") or 0))),
        "activated_at": value.get("activatedAt"),
        "registered_at": value.get("registeredAt"),
    }


def cloud_projection() -> dict[str, Any]:
    current = current_report()
    report = current.get("report") if isinstance(current.get("report"), dict) else {}
    scenario = report.get("scenarioStatus") if isinstance(report.get("scenarioStatus"), dict) else {}
    decisions = report.get("recentDecisions") if isinstance(report.get("recentDecisions"), list) else []
    compact_decisions = []
    for item in decisions[-20:]:
        if not isinstance(item, dict):
            continue
        compact_decisions.append({
            "id": _text(item.get("id"), 128),
            "type": _text(item.get("type"), 60),
            "model_id": _text(item.get("modelId"), 180),
            "from_channel": _text(item.get("fromChannel"), 30),
            "to_channel": _text(item.get("toChannel"), 30),
            "reason": _text(item.get("reason"), 500),
            "automatic": bool(item.get("automatic")),
            "at": item.get("at"),
        })
    compact_scenarios = {
        _text(model_id, 180): {
            "runs": max(0, int(summary.get("runs") or 0)),
            "pass_rate": _metric(summary.get("passRate")),
            "critical_failures": max(0, int(summary.get("criticalFailures") or 0)),
            "passed": bool(summary.get("passed")),
        }
        for model_id, summary in list(scenario.items())[:128]
        if isinstance(summary, dict)
    }
    health = health_summary()
    return {
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
        "schema_version": int(report.get("schemaVersion") or 1),
        "active": [_compact_model(item) for item in report.get("active", []) if isinstance(item, dict)],
        "shadow": [_compact_model(item) for item in report.get("shadow", []) if isinstance(item, dict)],
        "canary": [_compact_model(item) for item in report.get("canary", []) if isinstance(item, dict)],
        "degraded": [_compact_model(item) for item in report.get("degraded", []) if isinstance(item, dict)],
        "scenario_status": compact_scenarios,
        "recent_decisions": compact_decisions,
        "environment_profile_count": int(health["environment_profiles"]),
        "health_state": str(health["state"]),
        "summary_only": True,
        "cloud_read_only": True,
        "activation_authority": "local_only",
        "rollback_authority": "local_only",
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": MODEL_LIFECYCLE_VERSION,
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
        "authority": "local_tracky",
        "activation_authority": "local_only",
        "rollback_authority": "local_only",
        "cloud_read_only": True,
        "cloud_projection": "summary_only",
        "supports": [
            "shadow",
            "canary",
            "active",
            "drift_health",
            "known_good_rollback",
            "golden_scenarios",
            "environment_profiles",
        ],
    }

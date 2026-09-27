from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..database import db

MODEL_LIFECYCLE_PROTOCOL = "physical_model_lifecycle.v1"
MODEL_LIFECYCLE_VERSION = "2.77"
_ALLOWED_CHANNELS = {"active", "shadow", "canary", "retired", "rolled_back"}
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|"
    r"embedding|embeddings|face_embedding|transcript|filesystem_path|file_path|"
    r"source_uri|camera_uri|pixels|bytes|blob)(?:$|_)",
    re.IGNORECASE,
)


class TrackyModelLifecycleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = int(status_code)


def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    result = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not result:
        raise TrackyModelLifecycleError(f"{label} is required.")
    return result


def _boolean(value: Any) -> bool:
    return bool(value)


def _number(value: Any, minimum: float = 0.0, maximum: float = 1_000_000_000.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(minimum, min(maximum, number))


def _integer(value: Any, minimum: int = 0, maximum: int = 1_000_000_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(minimum, min(maximum, number))


def _assert_semantic(value: Any, path: str = "model_lifecycle", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyModelLifecycleError("Tracky model lifecycle nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 160:
            raise TrackyModelLifecycleError("Tracky model lifecycle object is too large.")
        for key, child in value.items():
            name = str(key)
            if _FORBIDDEN_KEY.search(name):
                raise TrackyModelLifecycleError(
                    f"Tracky model lifecycle contains local-only perception data at {path}.{name}."
                )
            _assert_semantic(child, f"{path}.{name}", depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 512:
            raise TrackyModelLifecycleError("Tracky model lifecycle list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, str) and len(value) > 20_000:
        raise TrackyModelLifecycleError("Tracky model lifecycle contains an oversized value.")


def _iso_timestamp(value: Any) -> str | None:
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
        raise TrackyModelLifecycleError("Tracky model lifecycle report is not valid JSON.") from exc


def _normalize_model(value: dict[str, Any], expected_channel: str) -> dict[str, Any]:
    channel = _text(value.get("channel") or expected_channel, 24).lower()
    if channel not in _ALLOWED_CHANNELS or channel != expected_channel:
        raise TrackyModelLifecycleError("Tracky model lifecycle channel is invalid.")
    canary = _number(value.get("canaryPercent"), 0.0, 100.0)
    if channel != "canary" and canary not in {0.0, 100.0}:
        canary = 100.0 if channel == "active" else 0.0
    return {
        "id": _text(value.get("id"), 180, required=True, label="model id"),
        "modelKey": _text(value.get("modelKey"), 128, required=True, label="modelKey"),
        "modelVersion": _text(value.get("modelVersion"), 80, required=True, label="modelVersion"),
        "channel": channel,
        "status": _text(value.get("status") or channel, 40),
        "knownGood": _boolean(value.get("knownGood")),
        "canaryPercent": canary,
        "registeredAt": _integer(value.get("registeredAt")),
        "activatedAt": _integer(value.get("activatedAt")),
        "retiredAt": _integer(value.get("retiredAt")),
        "previousActiveId": _text(value.get("previousActiveId") or "", 180) or None,
        "runtime": _text(value.get("runtime") or "tracky", 80),
    }


def _normalize_environment_profile(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "profileKey": _text(value.get("profileKey"), 128, required=True, label="profileKey"),
        "roomId": _text(value.get("roomId") or "", 128) or None,
        "cameraId": _text(value.get("cameraId") or "", 128) or None,
        "angleKey": _text(value.get("angleKey") or "default", 80),
        "lightingBucket": _text(value.get("lightingBucket") or "unknown", 60),
        "timeBucket": _text(value.get("timeBucket") or "any", 40),
        "seasonBucket": _text(value.get("seasonBucket") or "any", 40),
        "layoutFingerprint": _text(value.get("layoutFingerprint") or "", 128) or None,
        "modelKey": _text(value.get("modelKey"), 128, required=True, label="profile modelKey"),
        "modelVersion": _text(value.get("modelVersion"), 80, required=True, label="profile modelVersion"),
        "calibrationKey": _text(value.get("calibrationKey") or "", 128) or None,
        "updatedAt": _integer(value.get("updatedAt")),
    }


def _normalize_decision(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(value.get("id"), 128, required=True, label="decision id"),
        "type": _text(value.get("type"), 60),
        "modelId": _text(value.get("modelId"), 180),
        "fromChannel": _text(value.get("fromChannel") or "", 30),
        "toChannel": _text(value.get("toChannel") or "", 30),
        "reason": _text(value.get("reason") or "", 500),
        "automatic": _boolean(value.get("automatic")),
        "at": _integer(value.get("at")),
    }


def normalize_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrackyModelLifecycleError("Tracky model lifecycle report must be an object.")
    _assert_semantic(value)
    protocol = _text(value.get("protocol"), 80)
    if protocol != MODEL_LIFECYCLE_PROTOCOL:
        raise TrackyModelLifecycleError("Tracky model lifecycle protocol is unsupported.")

    active = [_normalize_model(item, "active") for item in (value.get("active") or [])[:32] if isinstance(item, dict)]
    shadow = [_normalize_model(item, "shadow") for item in (value.get("shadow") or [])[:32] if isinstance(item, dict)]
    canary = [_normalize_model(item, "canary") for item in (value.get("canary") or [])[:32] if isinstance(item, dict)]
    degraded = [_normalize_model(item, "active") for item in (value.get("degraded") or [])[:32] if isinstance(item, dict)]
    profiles = [
        _normalize_environment_profile(item)
        for item in (value.get("environmentProfiles") or [])[:256]
        if isinstance(item, dict)
    ]
    decisions = [
        _normalize_decision(item)
        for item in (value.get("recentDecisions") or [])[-50:]
        if isinstance(item, dict)
    ]
    scenario_status = value.get("scenarioStatus") if isinstance(value.get("scenarioStatus"), dict) else {}
    normalized_scenarios: dict[str, Any] = {}
    for model_id, item in list(scenario_status.items())[:64]:
        if not isinstance(item, dict):
            continue
        normalized_scenarios[_text(model_id, 180)] = {
            "runs": _integer(item.get("runs"), 0, 100_000),
            "passRate": None if item.get("passRate") is None else _number(item.get("passRate"), 0.0, 1.0),
            "criticalFailures": _integer(item.get("criticalFailures"), 0, 100_000),
            "passed": _boolean(item.get("passed")),
        }
    boundaries = [
        _text(item, 120)
        for item in (value.get("boundaries") if isinstance(value.get("boundaries"), list) else [])
        if _text(item, 120)
    ][:24]
    return {
        "schemaVersion": max(1, min(_integer(value.get("schemaVersion"), 1, 100), 100)),
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
        "generatedAt": _iso_timestamp(value.get("generatedAt")) or datetime.now(timezone.utc).isoformat(),
        "active": active,
        "shadow": shadow,
        "canary": canary,
        "degraded": degraded,
        "environmentProfiles": profiles,
        "recentDecisions": decisions,
        "scenarioStatus": normalized_scenarios,
        "boundaries": boundaries,
    }


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
            str(report["generatedAt"]),
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
    observed = _iso_timestamp(observed_at) or datetime.now(timezone.utc).isoformat()
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
            "activation_authority": "local_tracky",
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
        "activation_authority": "local_tracky",
        "fingerprint": str(row["fingerprint"] or ""),
        "generated_at": row["generated_at"],
        "observed_at": row["observed_at"],
        "updated_at": row["updated_at"],
        "source": str(row["source"] or "tracky"),
        "report": report if isinstance(report, dict) else {},
    }


def summary() -> dict[str, Any]:
    current = current_report()
    report = current.get("report") if isinstance(current.get("report"), dict) else {}
    active = report.get("active") if isinstance(report.get("active"), list) else []
    shadow = report.get("shadow") if isinstance(report.get("shadow"), list) else []
    canary = report.get("canary") if isinstance(report.get("canary"), list) else []
    degraded = report.get("degraded") if isinstance(report.get("degraded"), list) else []
    decisions = report.get("recentDecisions") if isinstance(report.get("recentDecisions"), list) else []
    last_decision = decisions[-1] if decisions else None
    return {
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
        "available": bool(current.get("available")),
        "authority": "local_tracky",
        "activation_authority": "local_tracky",
        "active_models": len(active),
        "shadow_models": len(shadow),
        "canary_models": len(canary),
        "degraded_models": len(degraded),
        "environment_profiles": len(report.get("environmentProfiles") or []),
        "last_decision_type": (last_decision or {}).get("type"),
        "last_decision_automatic": bool((last_decision or {}).get("automatic")),
        "generated_at": current.get("generated_at"),
        "observed_at": current.get("observed_at"),
    }


def health_state() -> dict[str, Any]:
    status = summary()
    if not status["available"]:
        state = "empty"
    elif status["degraded_models"] > 0:
        state = "degraded"
    elif status["canary_models"] > 0:
        state = "canary"
    elif status["shadow_models"] > 0:
        state = "evaluating"
    else:
        state = "healthy"
    return {**status, "state": state}


def _compact_model(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_key": _text(item.get("modelKey"), 128),
        "model_version": _text(item.get("modelVersion"), 80),
        "status": _text(item.get("status"), 40),
        "known_good": bool(item.get("knownGood")),
        "canary_percent": _number(item.get("canaryPercent"), 0.0, 100.0),
    }


def cloud_projection() -> dict[str, Any]:
    current = current_report()
    report = current.get("report") if isinstance(current.get("report"), dict) else {}
    decisions = report.get("recentDecisions") if isinstance(report.get("recentDecisions"), list) else []
    last = decisions[-1] if decisions else {}
    return {
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
        "schema_version": int(report.get("schemaVersion") or 1),
        "generated_at": current.get("generated_at"),
        "observed_at": current.get("observed_at"),
        "active_models": [_compact_model(item) for item in (report.get("active") or [])[:16] if isinstance(item, dict)],
        "shadow_models": [_compact_model(item) for item in (report.get("shadow") or [])[:16] if isinstance(item, dict)],
        "canary_models": [_compact_model(item) for item in (report.get("canary") or [])[:16] if isinstance(item, dict)],
        "degraded_models": [_compact_model(item) for item in (report.get("degraded") or [])[:16] if isinstance(item, dict)],
        "environment_profile_count": len(report.get("environmentProfiles") or []),
        "last_decision": {
            "type": _text(last.get("type") or "", 60),
            "automatic": bool(last.get("automatic")),
            "at": _integer(last.get("at")),
        } if last else None,
        "summary_only": True,
        "activation_authority": "local_tracky",
        "environment_profiles_exposed": False,
        "decision_history_exposed": False,
        "scenario_details_exposed": False,
    }


def public_capability() -> dict[str, Any]:
    status = health_state()
    return {
        "version": MODEL_LIFECYCLE_VERSION,
        "protocol": MODEL_LIFECYCLE_PROTOCOL,
        "available": bool(status["available"]),
        "authority": "local_tracky",
        "activation_authority": "local_tracky",
        "cloud_read_only": True,
        "cloud_projection": "summary_only",
        "shadow_mode": True,
        "canary_mode": True,
        "automatic_rollback": True,
        "environment_profiles_cloud": False,
        "decision_history_cloud": False,
        "scenario_details_cloud": False,
        "health": status["state"],
    }

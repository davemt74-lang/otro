from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..database import db

FORECAST_CALIBRATION_PROTOCOL = "forecast_calibration.v1"
FORECAST_CALIBRATION_VERSION = "2.76"
_ALLOWED_CHANNELS = {"active", "shadow", "canary"}
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|"
    r"embedding|embeddings|face_embedding|transcript|filesystem_path|file_path|"
    r"source_uri|camera_uri)(?:$|_)",
    re.IGNORECASE,
)


class TrackyCalibrationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = int(status_code)


def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    result = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not result:
        raise TrackyCalibrationError(f"{label} is required.")
    return result


def _metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _assert_semantic(value: Any, path: str = "forecast_calibration", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyCalibrationError("Tracky calibration payload nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 128:
            raise TrackyCalibrationError("Tracky calibration object is too large.")
        for key, child in value.items():
            name = str(key)
            if _FORBIDDEN_KEY.search(name):
                raise TrackyCalibrationError(
                    f"Tracky calibration contains local-only perception data at {path}.{name}."
                )
            _assert_semantic(child, f"{path}.{name}", depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 200:
            raise TrackyCalibrationError("Tracky calibration list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, str) and len(value) > 20000:
        raise TrackyCalibrationError("Tracky calibration contains an oversized value.")


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
        raise TrackyCalibrationError("Tracky calibration report is not valid JSON.") from exc


def _normalize_bucket(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": _text(value.get("key"), 32),
        "lower": _metric(value.get("lower")),
        "upper": _metric(value.get("upper")),
        "count": max(0, min(int(value.get("count") or 0), 10_000_000)),
        "weightedCount": max(0.0, min(float(value.get("weightedCount") or 0), 10_000_000.0)),
        "meanRawConfidence": _metric(value.get("meanRawConfidence")),
        "empiricalAccuracy": _metric(value.get("empiricalAccuracy")),
        "absoluteCalibrationError": _metric(value.get("absoluteCalibrationError")),
    }


def _normalize_profile(value: dict[str, Any]) -> dict[str, Any]:
    channel = _text(value.get("channel") or "active", 20).lower()
    if channel not in _ALLOWED_CHANNELS:
        raise TrackyCalibrationError("Tracky calibration profile channel is invalid.")
    raw_buckets = value.get("buckets") if isinstance(value.get("buckets"), list) else []
    buckets = [
        _normalize_bucket(item)
        for item in raw_buckets[:20]
        if isinstance(item, dict)
    ]
    return {
        "modelKey": _text(value.get("modelKey"), 128, required=True, label="modelKey"),
        "modelVersion": _text(value.get("modelVersion") or "unversioned", 80),
        "kind": _text(value.get("kind") or "generic", 80),
        "channel": channel,
        "settledCount": max(0, min(int(value.get("settledCount") or 0), 10_000_000)),
        "weightedCount": max(0.0, min(float(value.get("weightedCount") or 0), 10_000_000.0)),
        "meanRawConfidence": _metric(value.get("meanRawConfidence")),
        "empiricalAccuracy": _metric(value.get("empiricalAccuracy")),
        "brierScore": _metric(value.get("brierScore")),
        "expectedCalibrationError": _metric(value.get("expectedCalibrationError")),
        "buckets": buckets,
    }


def normalize_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrackyCalibrationError("Tracky calibration report must be an object.")
    _assert_semantic(value)
    schema_version = max(1, min(int(value.get("schemaVersion") or 1), 100))
    raw_profiles = value.get("profiles") if isinstance(value.get("profiles"), list) else []
    if len(raw_profiles) > 100:
        raise TrackyCalibrationError("Tracky calibration report contains too many model profiles.")
    profiles = [
        _normalize_profile(item)
        for item in raw_profiles
        if isinstance(item, dict)
    ]
    boundaries = [
        _text(item, 120)
        for item in (value.get("boundaries") if isinstance(value.get("boundaries"), list) else [])
        if _text(item, 120)
    ][:24]
    return {
        "schemaVersion": schema_version,
        "generatedAt": _iso_timestamp(value.get("generatedAt")) or datetime.now(timezone.utc).isoformat(),
        "predictions": max(0, min(int(value.get("predictions") or 0), 100_000_000)),
        "settlements": max(0, min(int(value.get("settlements") or 0), 100_000_000)),
        "profiles": profiles,
        "boundaries": boundaries,
    }


def _write_report(connection, report: dict[str, Any], observed_at: str, source: str) -> dict[str, Any]:
    encoded = _json(report)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    prior = connection.execute(
        "SELECT fingerprint FROM tracky_forecast_calibration WHERE id=1"
    ).fetchone()
    changed = prior is None or str(prior["fingerprint"] or "") != fingerprint
    connection.execute(
        """
        INSERT INTO tracky_forecast_calibration(
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
                WHEN tracky_forecast_calibration.fingerprint<>excluded.fingerprint
                THEN CURRENT_TIMESTAMP ELSE tracky_forecast_calibration.updated_at END
        """,
        (
            FORECAST_CALIBRATION_PROTOCOL,
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
            FROM tracky_forecast_calibration WHERE id=1
            """
        ).fetchone()
    if row is None:
        return {
            "protocol": FORECAST_CALIBRATION_PROTOCOL,
            "available": False,
            "authority": "local_tracky",
            "raw_predictions_exposed": False,
            "settlements_exposed": False,
            "report": {},
        }
    try:
        report = json.loads(str(row["report_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        report = {}
    available = bool(report and isinstance(report, dict))
    return {
        "protocol": str(row["protocol"] or FORECAST_CALIBRATION_PROTOCOL),
        "available": available,
        "authority": "local_tracky",
        "fingerprint": str(row["fingerprint"] or ""),
        "generated_at": row["generated_at"],
        "observed_at": row["observed_at"],
        "updated_at": row["updated_at"],
        "source": str(row["source"] or "tracky"),
        "raw_predictions_exposed": False,
        "settlements_exposed": False,
        "report": report if isinstance(report, dict) else {},
    }


def _weighted_average(profiles: list[dict[str, Any]], key: str) -> float | None:
    total = 0.0
    weight = 0.0
    for profile in profiles:
        value = profile.get(key)
        if value is None:
            continue
        count = max(0, int(profile.get("settledCount") or 0))
        if count <= 0:
            continue
        total += float(value) * count
        weight += count
    return total / weight if weight else None


def summary() -> dict[str, Any]:
    current = current_report()
    report = current.get("report") if isinstance(current.get("report"), dict) else {}
    profiles = report.get("profiles") if isinstance(report.get("profiles"), list) else []
    active = [item for item in profiles if isinstance(item, dict) and item.get("channel") == "active"]
    return {
        "protocol": FORECAST_CALIBRATION_PROTOCOL,
        "available": bool(current.get("available")),
        "authority": "local_tracky",
        "active_profiles": len(active),
        "active_settlements": sum(max(0, int(item.get("settledCount") or 0)) for item in active),
        "mean_brier_score": _weighted_average(active, "brierScore"),
        "mean_expected_calibration_error": _weighted_average(active, "expectedCalibrationError"),
        "generated_at": current.get("generated_at"),
        "observed_at": current.get("observed_at"),
        "raw_predictions_exposed": False,
        "settlements_exposed": False,
    }


def cloud_projection() -> dict[str, Any]:
    current = current_report()
    report = current.get("report") if isinstance(current.get("report"), dict) else {}
    profiles = report.get("profiles") if isinstance(report.get("profiles"), list) else []
    compact_profiles: list[dict[str, Any]] = []
    for item in profiles[:50]:
        if not isinstance(item, dict):
            continue
        compact_profiles.append({
            "model_key": _text(item.get("modelKey"), 128),
            "model_version": _text(item.get("modelVersion"), 80),
            "kind": _text(item.get("kind"), 80),
            "channel": _text(item.get("channel"), 20),
            "settled_count": max(0, int(item.get("settledCount") or 0)),
            "weighted_count": max(0.0, float(item.get("weightedCount") or 0)),
            "mean_original_confidence": _metric(item.get("meanRawConfidence")),
            "empirical_accuracy": _metric(item.get("empiricalAccuracy")),
            "brier_score": _metric(item.get("brierScore")),
            "expected_calibration_error": _metric(item.get("expectedCalibrationError")),
        })
    return {
        "protocol": FORECAST_CALIBRATION_PROTOCOL,
        "schema_version": int(report.get("schemaVersion") or 1),
        "generated_at": current.get("generated_at"),
        "observed_at": current.get("observed_at"),
        "predictions": max(0, int(report.get("predictions") or 0)),
        "settlements": max(0, int(report.get("settlements") or 0)),
        "profiles": compact_profiles,
        "summary_only": True,
        "prediction_records_exposed": False,
        "settlement_records_exposed": False,
    }


def public_capability() -> dict[str, Any]:
    status = summary()
    return {
        "version": FORECAST_CALIBRATION_VERSION,
        "protocol": FORECAST_CALIBRATION_PROTOCOL,
        "authority": "local_tracky",
        "available": bool(status["available"]),
        "cloud_projection": "summary_only",
        "raw_predictions_cloud": False,
        "settlements_cloud": False,
        "confidence_buckets_cloud": False,
        "metrics": [
            "settled_count",
            "empirical_accuracy",
            "brier_score",
            "expected_calibration_error",
        ],
    }

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="tracky-v276-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import tracky_forecast_calibration, tracky_physical_context  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == list(range(1, 49))
        table = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='tracky_forecast_calibration'
            """
        ).fetchone()
        assert table is not None

    report = {
        "schemaVersion": 1,
        "generatedAt": 1_800_000_000_000,
        "predictions": 48,
        "settlements": 32,
        "profiles": [
            {
                "modelKey": "location-model",
                "modelVersion": "2.76",
                "kind": "entity-location",
                "channel": "active",
                "settledCount": 20,
                "weightedCount": 19.0,
                "meanRawConfidence": 0.82,
                "empiricalAccuracy": 0.76,
                "brierScore": 0.18,
                "expectedCalibrationError": 0.07,
                "buckets": [
                    {
                        "key": "0.80-0.90",
                        "lower": 0.8,
                        "upper": 0.9,
                        "count": 12,
                        "weightedCount": 11.5,
                        "meanRawConfidence": 0.84,
                        "empiricalAccuracy": 0.78,
                        "absoluteCalibrationError": 0.06,
                    }
                ],
            },
            {
                "modelKey": "location-model",
                "modelVersion": "2.76",
                "kind": "entity-location",
                "channel": "shadow",
                "settledCount": 12,
                "weightedCount": 11.4,
                "meanRawConfidence": 0.88,
                "empiricalAccuracy": 0.67,
                "brierScore": 0.27,
                "expectedCalibrationError": 0.21,
                "buckets": [],
            },
        ],
        "boundaries": [
            "raw-predictions-immutable",
            "verified-outcome-authority-required",
            "minimum-evidence-before-calibration",
        ],
    }

    normalized = tracky_forecast_calibration.normalize_report(report)
    assert normalized["schemaVersion"] == 1
    assert normalized["predictions"] == 48
    assert normalized["settlements"] == 32
    assert normalized["profiles"][0]["brierScore"] == 0.18
    assert normalized["profiles"][0]["buckets"][0]["empiricalAccuracy"] == 0.78

    first = tracky_forecast_calibration.ingest_report(
        report,
        observed_at="2026-09-27T05:50:00+00:00",
        source="v276-test",
    )
    assert first["changed"] is True
    second = tracky_forecast_calibration.ingest_report(
        report,
        observed_at="2026-09-27T05:51:00+00:00",
        source="v276-test",
    )
    assert second["changed"] is False
    assert second["fingerprint"] == first["fingerprint"]

    current = tracky_forecast_calibration.current_report()
    assert current["available"] is True
    assert current["authority"] == "local_tracky"
    assert current["raw_predictions_exposed"] is False
    assert current["settlements_exposed"] is False
    assert current["report"]["profiles"][0]["buckets"]

    summary = tracky_forecast_calibration.summary()
    assert summary["available"] is True
    assert summary["active_profiles"] == 1
    assert summary["active_settlements"] == 20
    assert abs(float(summary["mean_brier_score"]) - 0.18) < 1e-9
    assert abs(float(summary["mean_expected_calibration_error"]) - 0.07) < 1e-9
    assert summary["raw_predictions_exposed"] is False
    assert summary["settlements_exposed"] is False

    cloud = tracky_forecast_calibration.cloud_projection()
    assert cloud["protocol"] == "forecast_calibration.v1"
    assert cloud["summary_only"] is True
    assert cloud["prediction_records_exposed"] is False
    assert cloud["settlement_records_exposed"] is False
    assert cloud["profiles"][0]["model_key"] == "location-model"
    assert cloud["profiles"][0]["mean_original_confidence"] == 0.82
    assert "mean_raw_confidence" not in cloud["profiles"][0]
    assert "buckets" not in cloud["profiles"][0]
    assert "outcomeValue" not in str(cloud)
    assert "predictedValue" not in str(cloud)

    capability = tracky_forecast_calibration.public_capability()
    assert capability["version"] == "2.76"
    assert capability["authority"] == "local_tracky"
    assert capability["cloud_projection"] == "summary_only"
    assert capability["raw_predictions_cloud"] is False
    assert capability["settlements_cloud"] is False
    assert capability["confidence_buckets_cloud"] is False

    bad = dict(report)
    bad["profiles"] = [
        {
            **report["profiles"][0],
            "metadata": {"frame_data": "private"},
        }
    ]
    try:
        tracky_forecast_calibration.normalize_report(bad)
        raise AssertionError("raw perception field entered calibration projection")
    except tracky_forecast_calibration.TrackyCalibrationError as exc:
        assert "local-only perception data" in str(exc)

    projection = {
        "events": [],
        "world_state": [],
        "context": {
            "current_room": "Office",
            "people_present": [],
            "recent_changes": [],
            "environment_status": "normal",
            "confidence": 0.9,
            "exceptions": [],
        },
        "context_sequence": 100,
        "context_observed_at": "2026-09-27T05:52:00+00:00",
        "forecast_calibration": report,
    }
    ingested = tracky_physical_context.ingest_semantic_projection(
        projection,
        source="v276-projection-test",
    )
    assert ingested["accepted"] is True
    assert ingested["forecast_calibration"]["accepted"] is True
    # Same report fingerprint was already stored, so ingestion is idempotent.
    assert ingested["forecast_calibration"]["changed"] is False

    context = tracky_physical_context.current_context()
    assert context["forecast_calibration"]["available"] is True
    assert context["forecast_calibration"]["report"]["profiles"][0]["brierScore"] == 0.18

    physical_cap = tracky_physical_context.public_capability()
    assert float(str(physical_cap["version"])) >= 2.76
    assert physical_cap["forecast_calibration"]["protocol"] == "forecast_calibration.v1"
    assert physical_cap["forecast_calibration"]["authority"] == "local_tracky"

    package = tracky_physical_context._cloud_payload()
    cloud_report = package["payload"]["forecast_calibration"]
    assert cloud_report["summary_only"] is True
    assert "buckets" not in str(cloud_report)
    assert package["payload"]["capabilities"]["forecast_calibration"] is True
    assert package["payload"]["capabilities"]["forecast_calibration_protocol"] == "forecast_calibration.v1"

    migration_source = (
        ROOT / "database" / "migrations" / "042_tracky_forecast_calibration.sql"
    ).read_text(encoding="utf-8")
    service_source = (
        ROOT / "app" / "services" / "tracky_forecast_calibration.py"
    ).read_text(encoding="utf-8")
    api_source = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    relay_source = (
        ROOT / "app" / "services" / "remote_bridge.py"
    ).read_text(encoding="utf-8")
    bridge_source = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")

    for forbidden_table in (
        "tracky_forecast_predictions",
        "tracky_forecast_settlements",
        "forecast_predictions",
        "forecast_settlements",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {forbidden_table}" not in migration_source

    assert "tracky_forecast_calibration" in migration_source
    assert "summary_only" in service_source
    assert "raw_predictions_cloud" in service_source
    assert '"/api/v1/tracky/calibration"' in api_source
    assert '@router.post("/api/v1/tracky/calibration")' not in api_source
    assert 'op == "physical_context.calibration"' in relay_source
    assert '"physical_context.calibration"' in bridge_source

print("Tracky V2.76 forecast calibration OTRO projection: PASS")

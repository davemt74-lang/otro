from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="tracky-v277-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import tracky_model_lifecycle, tracky_physical_context  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == list(range(1, 44))
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tracky_model_lifecycle'"
        ).fetchone()
        assert table is not None

    report = {
        "schemaVersion": 1,
        "protocol": "physical_model_lifecycle.v1",
        "active": [
            {
                "id": "detector@2",
                "modelKey": "detector",
                "modelVersion": "2",
                "channel": "active",
                "status": "active",
                "knownGood": True,
                "canaryPercent": 100,
                "registeredAt": 100,
                "activatedAt": 500,
                "retiredAt": 0,
                "previousActiveId": "detector@1",
                "runtime": "tracky",
            }
        ],
        "shadow": [
            {
                "id": "pose@4",
                "modelKey": "pose",
                "modelVersion": "4",
                "channel": "shadow",
                "status": "evaluating",
                "knownGood": False,
                "canaryPercent": 0,
                "registeredAt": 600,
                "activatedAt": 0,
                "retiredAt": 0,
                "previousActiveId": None,
                "runtime": "tracky",
            }
        ],
        "canary": [
            {
                "id": "object@3",
                "modelKey": "object",
                "modelVersion": "3",
                "channel": "canary",
                "status": "canary",
                "knownGood": False,
                "canaryPercent": 10,
                "registeredAt": 650,
                "activatedAt": 0,
                "retiredAt": 0,
                "previousActiveId": None,
                "runtime": "tracky",
            }
        ],
        "degraded": [],
        "environmentProfiles": [
            {
                "profileKey": "office-night-front",
                "roomId": "office",
                "cameraId": "cam-1",
                "angleKey": "front",
                "lightingBucket": "night-low",
                "timeBucket": "night",
                "seasonBucket": "any",
                "layoutFingerprint": "layout-v2",
                "modelKey": "detector",
                "modelVersion": "2",
                "calibrationKey": "cal-office-night",
                "updatedAt": 700,
            }
        ],
        "recentDecisions": [
            {
                "id": "decision-1",
                "type": "activate",
                "modelId": "detector@2",
                "fromChannel": "canary",
                "toChannel": "active",
                "reason": "canary_gate_green",
                "automatic": False,
                "at": 500,
            }
        ],
        "scenarioStatus": {
            "detector@2": {
                "runs": 6,
                "passRate": 1.0,
                "criticalFailures": 0,
                "passed": True,
            }
        },
        "boundaries": [
            "local-model-activation-authority",
            "calibration-consumed-not-duplicated",
            "rollback-known-good-only",
        ],
    }

    normalized = tracky_model_lifecycle.normalize_report(report)
    assert normalized["protocol"] == "physical_model_lifecycle.v1"
    assert normalized["active"][0]["modelVersion"] == "2"
    assert normalized["canary"][0]["canaryPercent"] == 10
    assert normalized["environmentProfiles"][0]["lightingBucket"] == "night-low"
    assert normalized["scenarioStatus"]["detector@2"]["passed"] is True

    first = tracky_model_lifecycle.ingest_report(
        report,
        observed_at="2026-09-27T08:40:00+00:00",
        source="v277-test",
    )
    assert first["changed"] is True
    second = tracky_model_lifecycle.ingest_report(
        report,
        observed_at="2026-09-27T08:41:00+00:00",
        source="v277-test",
    )
    assert second["changed"] is False
    assert second["fingerprint"] == first["fingerprint"]

    current = tracky_model_lifecycle.current_report()
    assert current["available"] is True
    assert current["authority"] == "local_tracky"
    assert current["activation_authority"] == "local_tracky"
    assert current["report"]["environmentProfiles"][0]["cameraId"] == "cam-1"

    summary = tracky_model_lifecycle.summary()
    assert summary["active_models"] == 1
    assert summary["shadow_models"] == 1
    assert summary["canary_models"] == 1
    assert summary["degraded_models"] == 0
    assert summary["environment_profiles"] == 1
    assert summary["last_decision_type"] == "activate"

    health = tracky_model_lifecycle.health_state()
    assert health["state"] == "canary"

    cloud = tracky_model_lifecycle.cloud_projection()
    assert cloud["protocol"] == "physical_model_lifecycle.v1"
    assert cloud["summary_only"] is True
    assert cloud["activation_authority"] == "local_tracky"
    assert cloud["environment_profiles_exposed"] is False
    assert cloud["decision_history_exposed"] is False
    assert cloud["scenario_details_exposed"] is False
    assert cloud["active_models"][0]["model_version"] == "2"
    assert cloud["canary_models"][0]["canary_percent"] == 10
    assert "environmentProfiles" not in cloud
    assert "scenarioStatus" not in cloud
    assert "reason" not in str(cloud.get("last_decision") or {})

    degraded = dict(report)
    degraded["degraded"] = [dict(report["active"][0], status="degraded")]
    tracky_model_lifecycle.ingest_report(degraded, source="v277-degraded")
    assert tracky_model_lifecycle.health_state()["state"] == "degraded"

    bad = dict(report)
    bad["environmentProfiles"] = [
        {**report["environmentProfiles"][0], "metadata": {"frame_data": "private"}}
    ]
    try:
        tracky_model_lifecycle.normalize_report(bad)
        raise AssertionError("raw perception data entered lifecycle projection")
    except tracky_model_lifecycle.TrackyModelLifecycleError as exc:
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
        "context_sequence": 200,
        "context_observed_at": "2026-09-27T08:42:00+00:00",
        "model_lifecycle": report,
    }
    ingested = tracky_physical_context.ingest_semantic_projection(
        projection,
        source="v277-projection-test",
    )
    assert ingested["accepted"] is True
    assert ingested["model_lifecycle"]["accepted"] is True

    context = tracky_physical_context.current_context()
    assert context["model_lifecycle"]["available"] is True
    assert context["model_lifecycle"]["report"]["active"][0]["modelVersion"] == "2"

    cap = tracky_physical_context.public_capability()
    assert cap["version"] == "2.77"
    assert cap["model_lifecycle"]["protocol"] == "physical_model_lifecycle.v1"
    assert cap["model_lifecycle"]["activation_authority"] == "local_tracky"
    assert cap["model_lifecycle"]["cloud_read_only"] is True
    assert cap["model_lifecycle"]["automatic_rollback"] is True

    package = tracky_physical_context._cloud_payload()
    lifecycle_wire = package["payload"]["model_lifecycle"]
    assert lifecycle_wire["summary_only"] is True
    assert package["payload"]["capabilities"]["model_lifecycle"] is True
    assert package["payload"]["capabilities"]["model_lifecycle_protocol"] == "physical_model_lifecycle.v1"
    assert package["payload"]["health"]["model_lifecycle"] in {"healthy", "evaluating", "canary", "degraded", "empty"}

    migration = (
        ROOT / "database" / "migrations" / "043_tracky_model_lifecycle.sql"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "app" / "services" / "tracky_model_lifecycle.py"
    ).read_text(encoding="utf-8")
    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    relay = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
    bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")

    for forbidden in (
        "tracky_model_lifecycle_decisions",
        "tracky_model_rollouts",
        "tracky_model_activations",
        "tracky_model_rollbacks",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {forbidden}" not in migration

    assert "activation_authority" in service
    assert "summary_only" in service
    assert '@router.get("/api/v1/tracky/model-lifecycle")' in api
    assert '@router.post("/api/v1/tracky/model-lifecycle")' not in api
    assert "promote" not in api
    assert "rollback" not in api
    assert 'op == "physical_context.model_lifecycle"' in relay
    assert '"physical_context.model_lifecycle"' in bridge

print("Tracky V2.77 OTRO model lifecycle projection: PASS")

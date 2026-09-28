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
        assert versions == list(range(1, 57))
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracky_model_lifecycle'"
        ).fetchone() is not None

    report = {
        "schemaVersion": 1,
        "protocol": "physical_model_lifecycle.v1",
        "active": [
            {
                "id": "location-model@2.76",
                "modelKey": "location-model",
                "modelVersion": "2.76",
                "channel": "active",
                "status": "active",
                "registeredAt": 1_800_000_000_000,
                "activatedAt": 1_800_000_100_000,
                "retiredAt": None,
                "knownGood": True,
                "canaryPercent": 100,
                "previousActiveId": "location-model@2.75",
                "packageChecksum": "sha256:local-private-package-checksum",
                "runtime": "tracky",
                "metadata": {"provider": "human-js", "semantic": True},
            }
        ],
        "shadow": [
            {
                "id": "location-model@2.77",
                "modelKey": "location-model",
                "modelVersion": "2.77",
                "channel": "shadow",
                "status": "evaluating",
                "registeredAt": 1_800_000_200_000,
                "knownGood": False,
                "canaryPercent": 0,
                "runtime": "tracky",
                "metadata": {"candidate": True},
            }
        ],
        "canary": [
            {
                "id": "object-model@4",
                "modelKey": "object-model",
                "modelVersion": "4",
                "channel": "canary",
                "status": "canary",
                "registeredAt": 1_800_000_200_000,
                "knownGood": False,
                "canaryPercent": 10,
                "runtime": "tracky",
                "metadata": {},
            }
        ],
        "degraded": [],
        "environmentProfiles": [
            {
                "profileKey": "office-camera-day",
                "roomId": "office",
                "cameraId": "camera-1",
                "angleKey": "desk",
                "lightingBucket": "day",
                "layoutFingerprint": "layout-secret-local",
                "timeBucket": "day",
                "seasonBucket": "summer",
                "modelKey": "location-model",
                "modelVersion": "2.76",
                "calibrationKey": "cal-office-day",
                "metadata": {"local_profile": True},
                "updatedAt": 1_800_000_300_000,
            }
        ],
        "recentDecisions": [
            {
                "id": "decision-register-1",
                "type": "register",
                "modelId": "location-model@2.77",
                "fromChannel": "",
                "toChannel": "shadow",
                "reason": "candidate_registered",
                "automatic": False,
                "metrics": {},
                "at": 1_800_000_200_000,
            }
        ],
        "scenarioStatus": {
            "location-model@2.76": {
                "runs": 6,
                "passRate": 1.0,
                "criticalFailures": 0,
                "passed": True,
            },
            "location-model@2.77": {
                "runs": 6,
                "passRate": 1.0,
                "criticalFailures": 0,
                "passed": True,
            },
        },
        "boundaries": [
            "local-model-activation-authority",
            "calibration-consumed-not-duplicated",
            "ground-truth-never-overridden",
            "rollback-known-good-only",
            "semantic-only-state",
        ],
    }

    normalized = tracky_model_lifecycle.normalize_report(report)
    assert normalized["protocol"] == "physical_model_lifecycle.v1"
    assert normalized["active"][0]["knownGood"] is True
    assert normalized["canary"][0]["canaryPercent"] == 10
    assert normalized["environmentProfiles"][0]["roomId"] == "office"

    first = tracky_model_lifecycle.ingest_report(
        report,
        observed_at="2026-09-27T14:30:00+00:00",
        source="v277-test",
    )
    assert first["changed"] is True
    second = tracky_model_lifecycle.ingest_report(
        report,
        observed_at="2026-09-27T14:31:00+00:00",
        source="v277-test",
    )
    assert second["changed"] is False
    assert second["fingerprint"] == first["fingerprint"]

    current = tracky_model_lifecycle.current_report()
    assert current["available"] is True
    assert current["authority"] == "local_tracky"
    assert current["report"]["active"][0]["modelVersion"] == "2.76"

    health = tracky_model_lifecycle.health_summary()
    assert health["state"] == "healthy"
    assert health["active_models"] == 1
    assert health["shadow_models"] == 1
    assert health["canary_models"] == 1
    assert health["degraded_models"] == 0
    assert health["environment_profiles"] == 1
    assert health["activation_authority"] == "local_only"
    assert health["rollback_authority"] == "local_only"
    assert health["cloud_read_only"] is True

    cloud = tracky_model_lifecycle.cloud_projection()
    assert cloud["protocol"] == "physical_model_lifecycle.v1"
    assert cloud["summary_only"] is True
    assert cloud["cloud_read_only"] is True
    assert cloud["activation_authority"] == "local_only"
    assert cloud["rollback_authority"] == "local_only"
    assert cloud["environment_profile_count"] == 1
    assert cloud["active"][0]["model_version"] == "2.76"
    assert cloud["canary"][0]["canary_percent"] == 10
    assert cloud["scenario_status"]["location-model@2.76"]["passed"] is True
    assert "package_checksum" not in str(cloud).lower()
    assert "layout-secret-local" not in str(cloud)
    assert "local_profile" not in str(cloud)
    assert "metadata" not in str(cloud)

    capability = tracky_model_lifecycle.public_capability()
    assert capability["version"] == "2.77"
    assert capability["authority"] == "local_tracky"
    assert capability["activation_authority"] == "local_only"
    assert capability["rollback_authority"] == "local_only"
    assert capability["cloud_read_only"] is True

    # A recent local automatic rollback is visible as recovering, not healthy.
    rollback_report = dict(report)
    rollback_report["recentDecisions"] = report["recentDecisions"] + [
        {
            "id": "decision-rollback-1",
            "type": "rollback",
            "modelId": "location-model@2.77",
            "fromChannel": "active",
            "toChannel": "rolled_back",
            "reason": "accuracy_regression",
            "automatic": True,
            "metrics": {"accuracy_delta": -0.12},
            "at": 1_800_000_400_000,
        }
    ]
    tracky_model_lifecycle.ingest_report(rollback_report, source="v277-test")
    assert tracky_model_lifecycle.health_summary()["state"] == "recovering"

    # Degraded active state takes precedence over rollback recovery.
    degraded_report = dict(rollback_report)
    degraded_model = dict(report["active"][0])
    degraded_model["status"] = "degraded"
    degraded_report["degraded"] = [degraded_model]
    tracky_model_lifecycle.ingest_report(degraded_report, source="v277-test")
    assert tracky_model_lifecycle.health_summary()["state"] == "degraded"

    # Raw/local sensor payloads are rejected before persistence or sync.
    bad = dict(report)
    bad["active"] = [dict(report["active"][0])]
    bad["active"][0]["metadata"] = {"frame_data": "private"}
    try:
        tracky_model_lifecycle.normalize_report(bad)
        raise AssertionError("raw perception entered model lifecycle projection")
    except tracky_model_lifecycle.TrackyLifecycleError as exc:
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
        "context_observed_at": "2026-09-27T14:35:00+00:00",
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
    assert context["model_lifecycle"]["report"]["active"][0]["modelKey"] == "location-model"

    physical_cap = tracky_physical_context.public_capability()
    assert physical_cap["version"] == "2.78"
    assert physical_cap["model_lifecycle"]["protocol"] == "physical_model_lifecycle.v1"
    assert physical_cap["model_lifecycle"]["activation_authority"] == "local_only"

    package = tracky_physical_context._cloud_payload()
    wire = package["payload"]["model_lifecycle"]
    assert wire["summary_only"] is True
    assert wire["cloud_read_only"] is True
    assert package["payload"]["capabilities"]["model_lifecycle"] is True
    assert package["payload"]["capabilities"]["model_lifecycle_protocol"] == "physical_model_lifecycle.v1"
    assert package["payload"]["health"]["model_lifecycle"] in {
        "healthy", "evaluating", "recovering", "degraded", "empty"
    }

    migration = (
        ROOT / "database" / "migrations" / "043_tracky_model_lifecycle.sql"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "app" / "services" / "tracky_model_lifecycle.py"
    ).read_text(encoding="utf-8")
    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    relay = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
    bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")

    for forbidden_table in (
        "tracky_model_candidates",
        "tracky_model_decisions",
        "tracky_model_accuracy",
        "tracky_model_scenarios",
        "tracky_environment_profiles",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {forbidden_table}" not in migration

    assert "tracky_model_lifecycle" in migration
    assert "activation_authority" in service
    assert "rollback_authority" in service
    assert '"/api/v1/tracky/model-lifecycle"' in api
    assert '@router.post("/api/v1/tracky/model-lifecycle' not in api
    assert "activate" not in "\n".join(
        line for line in api.splitlines()
        if "/api/v1/tracky/model-lifecycle" in line
    ).lower()
    assert 'op == "physical_context.model_lifecycle"' in relay
    assert '"physical_context.model_lifecycle"' in bridge

print("Tracky V2.77 OTRO model lifecycle integration: PASS")

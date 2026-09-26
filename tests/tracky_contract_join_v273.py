from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="tracky-v273-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import federated_data, room_device_automation, tracky_physical_context  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == list(range(1, 41))
        for table in (
            "tracky_physical_events",
            "tracky_physical_world_state",
            "tracky_physical_context",
            "tracky_active_perception_requests",
            "tracky_cloud_sync_state",
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None

    cap = tracky_physical_context.public_capability()
    assert cap["protocol"] == "physical_context.v1"
    assert cap["active_perception_protocol"] == "active_perception.v1"
    assert cap["continuity"]["foundation"] == "homeserver_v2.4"
    assert cap["privacy"]["semantic_projection_only"] is True
    assert cap["privacy"]["raw_frames_cloud_default"] is False
    assert cap["active_perception"]["physical_actions"] is False
    assert cap["provider"]["available"] is False

    # V2.4 reconciliation is authoritative: a fresh sensor read is blocked while
    # continuity is pending, before provider availability is even considered.
    blocked = tracky_physical_context.active_perception(
        "refresh_current_view",
        request_id="tracky-test-reconcile",
        correlation_id="tracky-corr-reconcile",
    )
    assert blocked["request"]["status"] == "unable"
    assert blocked["request"]["result"]["reason"] == "homeserver_reconciliation_pending"

    # Complete an empty authoritative full reconciliation using the real v2.4
    # engine; Tracky must not mutate or replace that state itself.
    datasets = {name: [] for name in federated_data.DATASETS}
    reconciled = federated_data.reconcile_snapshot(
        {
            "version": "2.2",
            "federation_version": "2.4",
            "authoritative_source": "vp3_cloud",
            "snapshot_mode": "full",
            "covered_datasets": list(datasets),
            "revision": "tracky-v273-test",
            "datasets": datasets,
        },
        observed_source="homeserver",
        trigger_reason="tracky-v273-test",
    )
    assert reconciled["status"] == "completed"
    assert federated_data.reconciliation_state("vp3_cloud")["needs_reconciliation"] is False

    no_provider = tracky_physical_context.active_perception(
        "refresh_current_view",
        request_id="tracky-test-provider",
        correlation_id="tracky-corr-provider",
    )
    assert no_provider["request"]["status"] == "unable"
    assert no_provider["request"]["result"]["reason"] == "provider_unavailable"

    room_device_automation.upsert_room(
        "office",
        "Office",
        description="Canonical VP3 OS room",
        enabled=True,
    )
    assert {room["room_id"] for room in tracky_physical_context.canonical_rooms()} == {"office"}

    semantic = {
        "events": [
            {
                "event_id": "tracky-event-0001",
                "sequence": 1,
                "event_type": "object.moved",
                "severity": "notable",
                "confidence": 0.96,
                "privacy_class": "cloud_derived",
                "occurred_at": "2026-09-26T12:00:00+00:00",
                "summary": "Keys moved to the desk",
                "room_id": "office",
                "subject": {"entity_id": "object:keys", "type": "object"},
            }
        ],
        "world_state": [
            {
                "subject_id": "object:keys",
                "predicate": "located_in",
                "object_id": "office",
                "confidence": 0.96,
                "temporal_state": "current",
                "source_event_id": "tracky-event-0001",
                "sequence": 1,
                "as_of": "2026-09-26T12:00:00+00:00",
            }
        ],
        "context": {
            "current_room": "Office",
            "people_present": [],
            "recent_changes": ["Keys moved to the desk"],
            "environment_status": "normal",
            "confidence": 0.96,
            "exceptions": [],
        },
        "context_sequence": 1,
        "context_observed_at": "2026-09-26T12:00:00+00:00",
    }
    first = tracky_physical_context.ingest_semantic_projection(semantic, source="test")
    assert first["inserted_events"] == 1
    duplicate = tracky_physical_context.ingest_semantic_projection(semantic, source="test")
    assert duplicate["duplicate_events"] == 1

    # Single-valued location state replaces the old object for the same
    # subject/predicate instead of accumulating multiple current rooms.
    semantic2 = {
        "events": [
            {
                "event_id": "tracky-event-0002",
                "sequence": 2,
                "event_type": "object.moved",
                "severity": "notable",
                "confidence": 0.98,
                "privacy_class": "cloud_derived",
                "occurred_at": "2026-09-26T12:01:00+00:00",
                "summary": "Keys moved to the kitchen",
                "subject": {"entity_id": "object:keys", "type": "object"},
            }
        ],
        "world_state": [
            {
                "subject_id": "object:keys",
                "predicate": "located_in",
                "object_id": "kitchen",
                "confidence": 0.98,
                "temporal_state": "current",
                "source_event_id": "tracky-event-0002",
                "sequence": 2,
                "as_of": "2026-09-26T12:01:00+00:00",
            }
        ],
        "context": {
            "current_room": "Office",
            "people_present": [],
            "recent_changes": ["Keys moved to the kitchen"],
            "environment_status": "normal",
            "confidence": 0.98,
            "exceptions": [],
        },
        "context_sequence": 2,
        "context_observed_at": "2026-09-26T12:01:00+00:00",
    }
    tracky_physical_context.ingest_semantic_projection(semantic2, source="test")
    current = tracky_physical_context.current_context()
    locations = [
        row for row in current["world_state"]
        if row["subject_id"] == "object:keys" and row["predicate"] == "located_in"
    ]
    assert len(locations) == 1
    assert locations[0]["object_id"] == "kitchen"

    # Raw perception is rejected at the semantic boundary.
    try:
        tracky_physical_context.ingest_semantic_projection(
            {"events": [], "context": {"raw_frame": "base64-data"}},
            source="test",
        )
        raise AssertionError("raw perception unexpectedly crossed the semantic boundary")
    except tracky_physical_context.TrackyPhysicalError as exc:
        assert exc.status_code == 422

    # Attach a deterministic provider that does not require a physical camera.
    # Active perception returns governed semantics through the existing relay;
    # outbound acknowledgement happens later in the normal HTTPS worker loop.
    def provider(request: dict) -> dict:
        assert request["request_type"] == "find_entity"
        assert request["target"]["entity_id"] == "object:glasses"
        return {
            "summary": "Glasses found in Office",
            "confidence": 0.99,
            "semantic_projection": {
                "events": [
                    {
                        "event_id": "tracky-event-0003",
                        "sequence": 3,
                        "event_type": "object.detected",
                        "severity": "notable",
                        "confidence": 0.99,
                        "privacy_class": "cloud_derived",
                        "occurred_at": "2026-09-26T12:02:00+00:00",
                        "summary": "Glasses found in Office",
                        "room_id": "office",
                        "subject": {"entity_id": "object:glasses", "type": "object"},
                    }
                ],
                "world_state": [
                    {
                        "subject_id": "object:glasses",
                        "predicate": "located_in",
                        "object_id": "office",
                        "confidence": 0.99,
                        "temporal_state": "current",
                        "source_event_id": "tracky-event-0003",
                        "sequence": 3,
                        "as_of": "2026-09-26T12:02:00+00:00",
                    }
                ],
                "context": {
                    "current_room": "Office",
                    "people_present": [],
                    "recent_changes": ["Glasses found in Office"],
                    "environment_status": "normal",
                    "confidence": 0.99,
                    "exceptions": [],
                },
                "context_sequence": 3,
                "context_observed_at": "2026-09-26T12:02:00+00:00",
            },
        }

    tracky_physical_context.register_provider(
        provider,
        name="test-provider",
        capabilities={"requires_camera": False, "object_tracking": True},
    )
    try:
        completed = tracky_physical_context.active_perception(
            "find_entity",
            request_id="tracky-test-completed",
            correlation_id="tracky-corr-completed",
            target={"entity_id": "object:glasses"},
        )
        assert completed["request"]["status"] == "completed"
        assert completed["request"]["result"]["reason"] == "completed"
        assert completed["request"]["result"]["provider_result"]["confidence"] == 0.99
        projection = completed["request"]["result"]["semantic_projection"]
        assert projection["protocol"] == "physical_context.v1"
        assert projection["site"]["id"].startswith("hs-")
        assert any(item["event_id"] == "tracky-event-0003" for item in projection["events"])
        assert completed["request"]["result"]["cloud_sync"]["deferred"] is True
        assert tracky_physical_context.request_status("tracky-test-completed")["request"]["status"] == "completed"
    finally:
        tracky_physical_context.unregister_provider()

    # Static architecture assertions: V2.73 must extend v2.4, never fork it.
    service_source = (ROOT / "app" / "services" / "tracky_physical_context.py").read_text(encoding="utf-8")
    relay_source = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
    bridge_source = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
    migration_source = (ROOT / "database" / "migrations" / "039_tracky_physical_context.sql").read_text(encoding="utf-8")

    assert 'federated_data.reconciliation_state("vp3_cloud")' in service_source
    assert "load_https_session()" in service_source
    assert "remote_identity_metadata()" in service_source
    assert "tracky-sync-v270.php" in service_source
    assert '"semantic_projection": semantic_projection' in service_source
    assert "tracky_physical_context.sync_cloud(timeout=8.0)" in relay_source
    assert "provider_unavailable" in service_source
    assert "privacy_engaged" in service_source
    assert "physical_actions" in service_source
    for operation in (
        "physical_context.capabilities",
        "physical_context.current",
        "physical_context.active_perception",
        "physical_context.request_status",
        "physical_context.sync",
    ):
        assert operation in relay_source
        assert operation in bridge_source

    forbidden_foundation = (
        "federated_record_links",
        "federated_sync_cursors",
        "federated_reconciliation_state",
        "federated_reconciliation_runs",
    )
    for table in forbidden_foundation:
        assert f"CREATE TABLE IF NOT EXISTS {table}" not in migration_source
    assert "native_authority_mirrored_continuity" not in service_source
    assert "reconcile_snapshot(" not in service_source

print("Tracky V2.73 OTRO contract join + active perception: PASS")

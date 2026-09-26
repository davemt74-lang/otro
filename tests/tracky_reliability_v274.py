from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="tracky-v274-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import federated_data, tracky_physical_context  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 41))

    vectors = json.loads(
        (ROOT / "tests" / "fixtures" / "tracky_v274_resilience_vectors.json").read_text(encoding="utf-8")
    )
    assert vectors["protocol"] == "physical_context.v1"
    assert vectors["active_perception_protocol"] == "active_perception.v1"
    indexed = {item["id"]: item for item in vectors["scenarios"]}
    assert indexed["cloud_outage_backoff"]["expected_backoff_seconds"] == [5, 10, 20, 40, 80, 160, 300]

    cap = tracky_physical_context.public_capability()
    assert cap["version"] == "2.74"
    assert cap["continuity"]["foundation"] == "homeserver_v2.4"
    assert cap["reliability"]["provider_timeout_seconds"] == 12
    assert cap["reliability"]["backlog_warn_events"] == 500
    assert cap["reliability"]["backlog_critical_events"] == 2000
    assert cap["active_perception"]["physical_actions"] is False

    # Establish canonical v2.4 reconciliation before exercising provider flow.
    datasets = {name: [] for name in federated_data.DATASETS}
    reconciled = federated_data.reconcile_snapshot(
        {
            "version": "2.2",
            "federation_version": "2.4",
            "authoritative_source": "vp3_cloud",
            "snapshot_mode": "full",
            "covered_datasets": list(datasets),
            "revision": "tracky-v274-test",
            "datasets": datasets,
        },
        observed_source="homeserver",
        trigger_reason="tracky-v274-test",
    )
    assert reconciled["status"] == "completed"
    assert federated_data.reconciliation_state("vp3_cloud")["needs_reconciliation"] is False

    event = {
        "events": [{
            "event_id": "tracky-v274-event-1",
            "sequence": 1,
            "event_type": "object.moved",
            "severity": "notable",
            "confidence": 0.93,
            "privacy_class": "cloud_derived",
            "occurred_at": "2026-09-26T19:00:00+00:00",
            "summary": "Keys moved",
            "subject": {"entity_id": "object:keys", "type": "object"},
        }],
        "world_state": [],
        "context": {
            "current_room": "Office",
            "people_present": [],
            "recent_changes": ["Keys moved"],
            "environment_status": "normal",
            "confidence": 0.93,
            "exceptions": [],
        },
        "context_sequence": 1,
        "context_observed_at": "2026-09-26T19:00:00+00:00",
    }
    first = tracky_physical_context.ingest_semantic_projection(event, source="v274-test")
    duplicate = tracky_physical_context.ingest_semantic_projection(event, source="v274-test")
    assert first["inserted_events"] == 1
    assert duplicate["duplicate_events"] == 1

    # Sync failure backoff grows exponentially and caps at five minutes.
    delays = []
    for expected in [5, 10, 20, 40, 80, 160, 300]:
        state = tracky_physical_context._record_sync_failure("simulated outage")
        delays.append(state["delay_seconds"])
        assert state["delay_seconds"] == expected
    assert delays == indexed["cloud_outage_backoff"]["expected_backoff_seconds"]
    assert tracky_physical_context.sync_due() is False

    with db() as connection:
        connection.execute(
            "UPDATE tracky_cloud_sync_state SET next_retry_at='2000-01-01T00:00:00+00:00' WHERE id=1"
        )
    assert tracky_physical_context.sync_due() is True
    tracky_physical_context._record_sync_success(1, "hs-1", 0)
    status = tracky_physical_context.sync_status()
    assert int(status["consecutive_failures"]) == 0
    assert status["next_retry_at"] is None
    assert status["last_error"] == ""

    # Requests left observing across a process interruption recover deterministically.
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_active_perception_requests(
                request_id,correlation_id,request_type,site_id,target_json,reason,status,
                requested_by,created_at,updated_at,deadline_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "tracky-stale-req", "tracky-stale-corr", "refresh_current_view", "stale-site",
                "{}", "stale test", "observing", "test",
                "2000-01-01 00:00:00", "2000-01-01 00:00:00", "2000-01-01T00:01:00+00:00",
            ),
        )
    assert tracky_physical_context.recover_stale_requests(30) == 1
    stale = tracky_physical_context.request_status("tracky-stale-req")["request"]
    assert stale["status"] == "failed"
    assert stale["result"]["reason"] == "request_recovered_after_interruption"

    # A new request in one correlation supersedes any still-active predecessor.
    identity = tracky_physical_context.remote_identity_metadata()
    site_id = identity["device_id"]
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_active_perception_requests(
                request_id,correlation_id,request_type,site_id,target_json,reason,status,
                requested_by,deadline_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "tracky-old-corr-request", "tracky-shared-correlation", "refresh_current_view",
                site_id, "{}", "old request", "observing", "test",
                "2099-01-01T00:00:00+00:00",
            ),
        )
    replacement = tracky_physical_context.active_perception(
        "refresh_current_view",
        request_id="tracky-new-corr-request",
        correlation_id="tracky-shared-correlation",
        site_id=site_id,
    )
    assert replacement["request"]["status"] == "unable"
    assert replacement["request"]["result"]["reason"] == "provider_unavailable"
    prior = tracky_physical_context.request_status("tracky-old-corr-request")["request"]
    assert prior["status"] == "superseded"
    assert prior["superseded_by"] == "tracky-new-corr-request"

    # Provider deadlines must fail closed rather than hanging the relay.
    def slow_provider(request: dict) -> dict:
        time.sleep(0.6)
        return {"summary": "late", "confidence": 1.0}

    tracky_physical_context.register_provider(
        slow_provider,
        name="slow-test-provider",
        capabilities={"requires_camera": False, "timeout_seconds": 0.25},
    )
    try:
        started = time.monotonic()
        timed = tracky_physical_context.active_perception(
            "refresh_current_view",
            request_id="tracky-timeout-request",
            correlation_id="tracky-timeout-correlation",
            site_id=site_id,
        )
        elapsed = time.monotonic() - started
        assert elapsed < 0.55
        assert timed["request"]["status"] == "failed"
        assert timed["request"]["result"]["reason"] == "provider_timeout"
    finally:
        tracky_physical_context.unregister_provider()

    migration = (ROOT / "database" / "migrations" / "040_tracky_reliability_hardening.sql").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "tracky_physical_context.py").read_text(encoding="utf-8")
    relay = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")

    for table in (
        "federated_record_links",
        "federated_sync_cursors",
        "federated_reconciliation_state",
        "federated_reconciliation_runs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" not in migration

    assert "TRACKY_SYNC_BACKOFF_MAX_SECONDS = 300" in service
    assert "recover_stale_requests" in service
    assert "_invoke_provider_with_timeout" in service
    assert "superseded_by_new_request" in service
    assert "tracky_physical_context.sync_due()" in relay
    assert "tracky_sync_state.get(\"pending_events\")" not in relay

print("Tracky V2.74 OTRO reliability hardening: PASS")

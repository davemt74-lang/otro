from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v246-reconcile-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import federated_data  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == list(range(1, 39))

    def full_snapshot(revision: str, knowledge_rows: list[dict], contacts_rows: list[dict] | None = None) -> dict:
        datasets = {name: [] for name in federated_data.DATASETS}
        datasets["knowledge"] = knowledge_rows
        datasets["contacts"] = contacts_rows or []
        return {
            "version": "2.2",
            "federation_version": "2.4",
            "authoritative_source": "vp3_cloud",
            "snapshot_mode": "full",
            "covered_datasets": list(datasets),
            "revision": revision,
            "datasets": datasets,
        }

    knowledge_a_v1 = federated_data.envelope(
        "vp3_cloud",
        "knowledge",
        "knowledge:alpha",
        title="Alpha",
        content="Alpha v1",
        updated_at="2026-09-26T10:00:00Z",
    )
    knowledge_b_v1 = federated_data.envelope(
        "vp3_cloud",
        "knowledge",
        "knowledge:beta",
        title="Beta",
        content="Beta v1",
        updated_at="2026-09-26T10:00:00Z",
    )
    contact_v1 = federated_data.envelope(
        "vp3_cloud",
        "contacts",
        "contact:one",
        title="Contact One",
        content="contact@example.test",
        updated_at="2026-09-26T10:00:00Z",
    )

    first = federated_data.reconcile_snapshot(
        full_snapshot("rev-full-1", [knowledge_a_v1, knowledge_b_v1], [contact_v1]),
        observed_source="homeserver",
        trigger_reason="initial-pair",
    )
    assert first["status"] == "completed"
    assert first["snapshot_mode"] == "full"
    assert first["created"] == 3
    assert first["updated"] == 0
    assert first["tombstoned"] == 0
    assert first["conflicts"] == 0
    assert federated_data.reconciliation_state("vp3_cloud")["needs_reconciliation"] is False

    disconnected = federated_data.note_peer_disconnected(
        "vp3_cloud", "simulated offline interval"
    )
    assert disconnected["needs_reconciliation"] is True
    reconnected = federated_data.note_peer_connected("vp3_cloud")
    assert reconnected["reconnected"] is True
    assert reconnected["needs_reconciliation"] is True

    # A filtered query while reconnect is pending may refresh a record but must
    # never infer deletion from absence.
    knowledge_a_v2 = federated_data.envelope(
        "vp3_cloud",
        "knowledge",
        "knowledge:alpha",
        title="Alpha",
        content="Alpha v2 while offline",
        updated_at="2026-09-26T11:00:00Z",
    )
    filtered = {
        "authoritative_source": "vp3_cloud",
        "snapshot_mode": "filtered",
        "revision": "rev-filtered-2",
        "datasets": {"knowledge": [knowledge_a_v2]},
    }
    filtered_result = federated_data.reconcile_snapshot(
        filtered,
        observed_source="homeserver",
        trigger_reason="query-before-full-reconcile",
    )
    assert filtered_result["updated"] == 1
    assert filtered_result["tombstoned"] == 0
    assert federated_data.reconciliation_state("vp3_cloud")["needs_reconciliation"] is True
    assert (
        federated_data.resolve_authority_key(
            knowledge_b_v1["canonical_id"],
            authority_source="vp3_cloud",
            dataset="knowledge",
            observed_source="homeserver",
        )
        == "knowledge:beta"
    )

    knowledge_c_v1 = federated_data.envelope(
        "vp3_cloud",
        "knowledge",
        "knowledge:gamma",
        title="Gamma",
        content="Created while HomeServer was offline",
        updated_at="2026-09-26T11:05:00Z",
    )
    second = federated_data.reconcile_snapshot(
        full_snapshot("rev-full-2", [knowledge_a_v2, knowledge_c_v1], [contact_v1]),
        observed_source="homeserver",
        trigger_reason="reconnect",
    )
    assert second["status"] == "completed"
    assert second["created"] == 1
    assert second["updated"] == 0
    assert second["unchanged"] >= 2
    assert second["tombstoned"] == 1
    assert second["datasets"]["knowledge"]["tombstoned"] == 1
    state = federated_data.reconciliation_state("vp3_cloud")
    assert state["needs_reconciliation"] is False
    assert state["last_snapshot_revision"] == "rev-full-2"
    assert state["last_run_id"] == second["run_id"]

    assert (
        federated_data.resolve_authority_key(
            knowledge_b_v1["canonical_id"],
            authority_source="vp3_cloud",
            dataset="knowledge",
            observed_source="homeserver",
        )
        is None
    )
    assert (
        federated_data.resolve_authority_key(
            knowledge_b_v1["canonical_id"],
            authority_source="vp3_cloud",
            dataset="knowledge",
            observed_source="homeserver",
            include_tombstoned=True,
        )
        == "knowledge:beta"
    )

    # Native authority can restore a previously deleted record with the same
    # canonical identity.
    knowledge_b_v2 = federated_data.envelope(
        "vp3_cloud",
        "knowledge",
        "knowledge:beta",
        title="Beta restored",
        content="Beta restored after reconnect",
        updated_at="2026-09-26T11:10:00Z",
    )
    third = federated_data.reconcile_snapshot(
        full_snapshot(
            "rev-full-3",
            [knowledge_a_v2, knowledge_b_v2, knowledge_c_v1],
            [contact_v1],
        ),
        observed_source="homeserver",
        trigger_reason="subsequent-full-sync",
    )
    assert third["restored"] == 1
    assert third["datasets"]["knowledge"]["restored"] == 1
    assert (
        federated_data.resolve_authority_key(
            knowledge_b_v2["canonical_id"],
            authority_source="vp3_cloud",
            dataset="knowledge",
            observed_source="homeserver",
        )
        == "knowledge:beta"
    )

    # Duplicate authority keys are rejected before any mirror mutation.
    with db() as connection:
        before_hash = connection.execute(
            """
            SELECT record_hash FROM federated_record_links
            WHERE authority_source='vp3_cloud' AND dataset='knowledge'
              AND authority_key='knowledge:alpha' AND observed_source='homeserver'
            """
        ).fetchone()["record_hash"]

    duplicate_a = dict(knowledge_a_v2)
    duplicate_a["content"] = "This must never partially apply."
    duplicate_a["record_revision"] = federated_data.record_revision(
        duplicate_a["title"], duplicate_a["content"], duplicate_a["updated_at"]
    )
    bad = full_snapshot(
        "rev-bad-duplicate",
        [knowledge_a_v2, duplicate_a, knowledge_c_v1],
        [contact_v1],
    )
    try:
        federated_data.reconcile_snapshot(
            bad,
            observed_source="homeserver",
            trigger_reason="malformed-reconnect",
        )
        raise AssertionError("duplicate authority key snapshot should fail")
    except federated_data.FederatedDataError:
        pass

    with db() as connection:
        after_hash = connection.execute(
            """
            SELECT record_hash FROM federated_record_links
            WHERE authority_source='vp3_cloud' AND dataset='knowledge'
              AND authority_key='knowledge:alpha' AND observed_source='homeserver'
            """
        ).fetchone()["record_hash"]
    assert after_hash == before_hash
    failed_state = federated_data.reconciliation_state("vp3_cloud")
    assert failed_state["needs_reconciliation"] is True
    assert failed_state["last_error"]

    runs = federated_data.recent_reconciliation_runs(20)
    assert any(item["status"] == "failed" for item in runs)
    assert any(
        item["status"] == "completed"
        and item["snapshot_mode"] == "full"
        and item["tombstoned_count"] == 1
        for item in runs
    )

    # Full snapshots must explicitly carry every declared dataset so absence can
    # only be interpreted as deletion when coverage is complete.
    try:
        federated_data.reconcile_snapshot(
            {
                "authoritative_source": "vp3_cloud",
                "snapshot_mode": "full",
                "covered_datasets": ["knowledge", "contacts"],
                "revision": "rev-incomplete",
                "datasets": {"knowledge": []},
            },
            observed_source="homeserver",
        )
        raise AssertionError("incomplete full snapshot should fail")
    except federated_data.FederatedDataError:
        pass

remote_bridge_source = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
shared_source = (ROOT / "app" / "services" / "shared_agent_context.py").read_text(encoding="utf-8")
capabilities = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")

assert 'note_peer_disconnected("vp3_cloud"' in remote_bridge_source
assert 'note_peer_connected("vp3_cloud")' in remote_bridge_source
assert '"bridge.reconnected" if peer_state.get("reconnected") else "bridge.connected"' in remote_bridge_source
assert '"reconciliation": federated_data.reconciliation_state("vp3_cloud")' in remote_bridge_source
assert '"snapshot_mode": "full" if not text else "filtered"' in shared_source
assert "federated_data.reconcile_snapshot(" in shared_source
assert '"disconnect_reconnect_reconciliation"' in capabilities
assert '"absence_tombstones_full_snapshots_only": True' in capabilities
assert '"filtered_snapshots_never_delete": True' in capabilities

print("HomeServer v2.4 Section 7 Disconnect/Reconnect reconciliation: PASS")

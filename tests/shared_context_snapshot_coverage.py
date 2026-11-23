"""Bounded context exports cannot become false deletion authority."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="shared-context-coverage-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    from app.database import db, initialize_database
    from app.services import federated_data, knowledge, memory_continuity, shared_agent_context as shared

    initialize_database()
    knowledge.ensure_knowledge_index()
    for number in range(41):
        knowledge.create_knowledge_item(f"Native note {number}", "note", f"Content {number}")
    all_items = knowledge.list_knowledge("", limit=100)
    original = {"authoritative_source": "homeserver", "snapshot_mode": "full", "covered_datasets": ["knowledge"],
                "datasets": {"knowledge": [shared._sanitize_record("homeserver", "knowledge", row, number)
                                             for number, row in enumerate(all_items)]}}
    federated_data.reconcile_snapshot(original, observed_source="vp3_cloud")
    page = shared.local_snapshot()
    assert len(page["datasets"]["knowledge"]) == 40
    assert page["snapshot_mode"] == "full" and "knowledge" not in page["covered_datasets"]
    assert page["unavailable_datasets"]["knowledge"] == "snapshot_limited:record_limit"
    result = federated_data.reconcile_snapshot(page, observed_source="vp3_cloud")
    assert result["tombstoned"] == 0
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 41
        assert connection.execute("SELECT COUNT(*) FROM federated_record_links WHERE authority_source='homeserver' AND dataset='knowledge' AND observed_source='vp3_cloud' AND tombstoned=0").fetchone()[0] == 41
    print("PASS forty-one existing Knowledge items retain all mirrors when the bounded export contains only forty")

    # Hitting the byte ceiling is distinct from a record cap. Do not allow
    # trimming a complete forty-row memory set to tombstone its missing tail.
    for number in range(40):
        memory_continuity.create_federated_memory({"memory_key": f"large:{number}", "content": "M" * 4000}, source_app_key="owner", owner=True)
    with db() as connection:
        connection.execute("UPDATE knowledge_items SET content=?", ("K" * 4000,))
    full_memories = shared._local_memory("")
    federated_data.reconcile_snapshot({"authoritative_source": "homeserver", "snapshot_mode": "full",
        "covered_datasets": ["memory"], "datasets": {"memory": full_memories}}, observed_source="vp3_cloud")
    page = shared.local_snapshot()
    assert len(page["datasets"]["memory"]) < 40
    assert page["unavailable_datasets"]["memory"] == "snapshot_limited:byte_limit"
    assert "memory" not in page["covered_datasets"]
    assert federated_data.reconcile_snapshot(page, observed_source="vp3_cloud")["tombstoned"] == 0
    print("PASS byte-budget trimming preserves existing memory mirrors outside the transmitted page")

    cloud = {"authoritative_source": "vp3_cloud", "snapshot_mode": "full", "revision": "valid-cloud-revision",
             "covered_datasets": ["memory"], "datasets": {"memory": [{"id": "old-cloud-memory", "title": "Preserved Cloud memory", "content": "Still exists"}]}}
    shared.apply_cloud_snapshot(cloud)
    malformed = copy.deepcopy(cloud)
    malformed["covered_datasets"].append("contacts")
    malformed["datasets"]["memory"] = []
    malformed["revision"] = "malformed-cloud-revision"
    try:
        shared.apply_cloud_snapshot(malformed)
        raise AssertionError("Missing full dataset was normalized into deletion authority")
    except shared.SharedAgentContextError:
        pass
    assert shared.cloud_snapshot()["revision"] == "valid-cloud-revision"
    assert shared.cloud_candidates("memory", "Preserved")
    for invalid_rows in ([None], [{"id": "replacement", "title": "Replacement", "content": "Valid row"}, "invalid"]):
        malformed_rows = copy.deepcopy(cloud)
        malformed_rows["revision"] = "invalid-covered-records"
        malformed_rows["datasets"]["memory"] = invalid_rows
        try:
            shared.apply_cloud_snapshot(malformed_rows)
            raise AssertionError("Invalid full records were silently converted into deletion authority")
        except shared.SharedAgentContextError:
            pass
        assert shared.cloud_snapshot()["revision"] == "valid-cloud-revision"
        assert shared.cloud_candidates("memory", "Preserved")
    oversized = copy.deepcopy(cloud)
    oversized["covered_datasets"].append("contacts")
    oversized["datasets"]["contacts"] = []
    oversized["datasets"]["memory"] = [{"id": f"incoming-{n}", "title": f"Incoming {n}", "content": "Bounded"} for n in range(101)]
    result = shared.apply_cloud_snapshot(oversized)
    assert result["reconciliation"]["snapshot_mode"] == "full"
    assert result["reconciliation"]["covered_datasets"] == ["contacts"]
    assert result["reconciliation"]["tombstoned"] == 0
    assert shared.cloud_snapshot()["unavailable_datasets"]["memory"] == "snapshot_limited:record_limit"
    oversized["covered_datasets"] = ["memory"]
    result = shared.apply_cloud_snapshot(oversized)
    assert result["reconciliation"]["snapshot_mode"] == "filtered" and result["reconciliation"]["tombstoned"] == 0
    print("PASS malformed full snapshots preserve the last cache; oversized inbound pages cannot authorize deletion")

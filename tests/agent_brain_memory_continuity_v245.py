from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v245-agent-brain-memory-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        action_policy,
        app_scopes,
        approvals,
        cognitive_runtime,
        federated_data,
        memory_continuity,
        pairing,
        shared_agent_context,
        tools,
    )

    initialize_database()

    with db() as connection:
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == list(range(1, 38))

    migration = (
        ROOT / "database" / "migrations" / "037_agent_brain_memory_continuity.sql"
    ).read_text(encoding="utf-8")
    for action_key in ("memory.write", "memory.update", "memory.delete"):
        assert action_key in migration
    assert "federated_memory_mutations" in migration
    assert "app_permissions" not in migration

    request = pairing.create_pairing_request(
        "vp3",
        "VP3",
        ["tools.execute", "memory.read", "memory.write"],
    )
    assert pairing.approve_pairing_request(request["request_id"]) is not None

    tool_items = {
        item["key"]: item
        for item in tools.list_tools(
            {"tools.execute", "memory.read", "memory.write"}
        )
    }
    for key in ("memory.list", "memory.write", "memory.update", "memory.delete"):
        assert key in tool_items
        assert tool_items[key]["available"] is True

    assert action_policy.default_mode("memory.write") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("memory.update") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("memory.delete") == action_policy.APPROVAL_REQUIRED

    private_content = "Section 6 private memory content must stay out of audit metadata."
    create_request = approvals.create_memory_write_request(
        "app:vp3",
        {
            "mutation_id": "memory-create-v245-001",
            "memory_key": "project:section6",
            "content": private_content,
            "importance": 0.91,
            "memory_type": "semantic",
            "confidence": 0.87,
            "entity_type": "project",
            "entity_key": "homeserver-v2.4-section6",
        },
        owner=False,
    )
    request_id = str(create_request["result"]["request_id"])
    with db() as connection:
        queued = connection.execute(
            """
            SELECT arguments_json,arguments_meta_json
            FROM action_requests WHERE id=? LIMIT 1
            """,
            (request_id,),
        ).fetchone()
    assert queued is not None
    arguments = json.loads(queued["arguments_json"])
    assert arguments["content"] == private_content
    assert arguments["mutation_id"] == "memory-create-v245-001"
    assert private_content not in queued["arguments_meta_json"]
    assert '"content_length"' in queued["arguments_meta_json"]

    approved = approvals.approve_request(request_id)
    assert approved["status"] == "executed"

    memories = memory_continuity.list_federated_memories(
        "section6",
        20,
        source_app_key="app:vp3",
        owner=False,
    )
    assert len(memories) == 1
    memory = memories[0]
    assert memory["authority_source"] == "homeserver"
    assert memory["authority_key"].startswith("agent_memory:")
    assert memory["canonical_id"] == federated_data.canonical_id(
        "homeserver", "memory", memory["authority_key"]
    )
    assert len(memory["record_revision"]) == 64
    assert memory["memory_type"] == "semantic"
    assert memory["confidence"] == 0.87
    assert memory["entity_key"] == "homeserver-v2.4-section6"
    original_revision = str(memory["record_revision"])
    canonical = str(memory["canonical_id"])

    replay = memory_continuity.create_federated_memory(
        arguments,
        source_app_key="app:vp3",
        owner=False,
    )
    assert replay["idempotent_replay"] is True
    assert replay["memory"]["canonical_id"] == canonical
    assert len(
        memory_continuity.list_federated_memories(
            "section6", 20, source_app_key="app:vp3", owner=False
        )
    ) == 1

    update_request = approvals.create_memory_update_request(
        "app:vp3",
        {
            "canonical_id": canonical,
            "mutation_id": "memory-update-v245-001",
            "expected_revision": original_revision,
            "content": "Section 6 updated canonical memory.",
            "confidence": 0.94,
            "memory_type": "semantic",
        },
        owner=False,
    )
    approvals.approve_request(str(update_request["result"]["request_id"]))
    updated = memory_continuity.get_federated_memory_by_canonical(canonical)
    assert updated is not None
    assert updated["content"] == "Section 6 updated canonical memory."
    assert updated["confidence"] == 0.94
    assert updated["canonical_id"] == canonical
    assert updated["record_revision"] != original_revision
    current_revision = str(updated["record_revision"])

    try:
        memory_continuity.update_federated_memory(
            {
                "canonical_id": canonical,
                "mutation_id": "memory-update-v245-stale",
                "expected_revision": original_revision,
                "content": "Stale overwrite must fail.",
            },
            source_app_key="app:vp3",
            owner=False,
        )
        raise AssertionError("stale memory revision should fail")
    except memory_continuity.MemoryContinuityError as exc:
        assert exc.status_code == 409

    cloud_memory = federated_data.canonical_id(
        "vp3_cloud",
        "memory",
        "agent_memory_item:999",
    )
    try:
        memory_continuity.update_federated_memory(
            {
                "canonical_id": cloud_memory,
                "mutation_id": "memory-cross-authority-001",
                "expected_revision": "0" * 64,
                "content": "Must not write Cloud-authoritative memory locally.",
            },
            source_app_key="app:vp3",
            owner=False,
        )
        raise AssertionError("Cloud-authoritative memory mutated on HomeServer")
    except memory_continuity.MemoryContinuityError as exc:
        assert exc.status_code in {404, 409}

    scoped_request = pairing.create_pairing_request(
        "scoped",
        "Scoped Memory App",
        ["tools.execute", "memory.read", "memory.write"],
    )
    assert pairing.approve_pairing_request(scoped_request["request_id"]) is not None
    with db() as connection:
        scoped_app = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key='scoped'"
        ).fetchone()
    assert scoped_app is not None
    app_scopes.save_scope(
        int(scoped_app["id"]),
        {
            "cloud_allowed": True,
            "memory_key_prefixes": ["allowed:"],
            "knowledge_kinds": [],
            "tool_names": [],
            "plugin_keys": [],
        },
    )

    allowed = memory_continuity.create_federated_memory(
        {
            "memory_key": "allowed:status",
            "content": "Visible only inside the scoped app.",
            "memory_type": "preference",
        },
        source_app_key="owner",
        owner=True,
    )
    blocked = memory_continuity.create_federated_memory(
        {
            "memory_key": "private:status",
            "content": "Must be filtered from the scoped app.",
            "memory_type": "preference",
        },
        source_app_key="owner",
        owner=True,
    )
    scoped_rows = memory_continuity.list_federated_memories(
        "",
        50,
        source_app_key="app:scoped",
        owner=False,
    )
    scoped_ids = {item["canonical_id"] for item in scoped_rows}
    assert allowed["memory"]["canonical_id"] in scoped_ids
    assert blocked["memory"]["canonical_id"] not in scoped_ids

    try:
        memory_continuity.create_federated_memory(
            {
                "mutation_id": "scoped-memory-block-001",
                "memory_key": "private:blocked",
                "content": "This scope escape must fail.",
            },
            source_app_key="app:scoped",
            owner=False,
        )
        raise AssertionError("memory key scope escape was accepted")
    except memory_continuity.MemoryContinuityError as exc:
        assert exc.status_code == 403

    candidate_event = cognitive_runtime.emit_event(
        source_app_key="app:vp3",
        source_kind="app",
        event_id="section6-memory-candidate",
        event_type="agent.preference",
        summary="The user prefers concise continuity status updates.",
        entity_type="preference",
        entity_key="continuity-status-style",
        importance=0.9,
        memory_candidate=True,
        memory_type="preference",
        memory_key="preference:continuity-status",
        allow_memory_candidate=True,
    )
    assert candidate_event["event"]["memory_candidate"] is True
    jobs = cognitive_runtime.process_pending_jobs(limit=50)
    assert jobs["failed"] == 0
    candidate = next(
        item
        for item in cognitive_runtime.list_memory_candidates(limit=50, status="pending")
        if item.get("source_event_id") == candidate_event["event"]["id"]
    )
    accepted = cognitive_runtime.decide_memory_candidate(candidate["id"], "accepted")
    assert accepted["status"] == "accepted"
    promoted = memory_continuity.get_federated_memory(
        int(accepted["consolidated_memory_id"])
    )
    assert promoted is not None
    assert promoted["memory_type"] == "preference"
    assert promoted["source_app_key"] == "app:vp3"
    assert promoted["entity_key"] == "continuity-status-style"
    assert promoted["canonical_id"].startswith("fd24_")

    snapshot = shared_agent_context.local_snapshot("continuity")
    shared_memories = snapshot["datasets"]["memory"]
    assert shared_memories
    shared_by_id = {
        str(item["canonical_id"]): item for item in shared_memories
    }
    assert promoted["canonical_id"] in shared_by_id
    assert shared_by_id[promoted["canonical_id"]]["record_revision"] == promoted["record_revision"]

    delete_request = approvals.create_memory_delete_request(
        "app:vp3",
        {
            "canonical_id": canonical,
            "mutation_id": "memory-delete-v245-001",
            "expected_revision": current_revision,
        },
        owner=False,
    )
    approvals.approve_request(str(delete_request["result"]["request_id"]))
    assert memory_continuity.get_federated_memory_by_canonical(canonical) is None
    tombstone = federated_data.resolve_authority_key(
        canonical,
        authority_source="homeserver",
        dataset="memory",
        observed_source="homeserver",
        include_tombstoned=True,
    )
    assert tombstone is not None and tombstone.startswith("agent_memory:")

    with db() as connection:
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM federated_memory_mutations"
        ).fetchone()[0]
        assert receipt_count >= 3
        action_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='action_requests'"
        ).fetchone()[0]
        for action_key in ("memory.write", "memory.update", "memory.delete"):
            assert f"'{action_key}'" in action_schema

    capabilities = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
    assert '"agent_brain_memory_continuity"' in capabilities
    assert '"memory_key_scopes_enforced": True' in capabilities
    assert '"candidate_promotion_preserved": True' in capabilities

print("HomeServer v2.4 Section 6 Agent Brain & Memory continuity: PASS")

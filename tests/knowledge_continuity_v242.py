from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v242-knowledge-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import action_policy, approvals, federated_data, knowledge, knowledge_collections, knowledge_collection_policy, pairing, tools  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == list(range(1, 38))

    migration = (ROOT / "database" / "migrations" / "034_governed_knowledge_actions.sql").read_text(encoding="utf-8")
    migration_sql = "\\n".join(
        line for line in migration.splitlines()
        if not line.lstrip().startswith("--")
    )
    for action_key in ("knowledge.create", "knowledge.update", "knowledge.delete"):
        assert action_key in migration_sql
    assert "app_permissions" not in migration_sql
    assert "knowledge.write" not in migration_sql

    bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
    assert '"knowledge_continuity"' in bridge
    assert '"watched_folder_items_read_only": True' in bridge
    assert '"file_backed_items_read_only": True' in bridge
    assert '"governed_writes": True' in bridge

    assert "knowledge.write" in pairing.DEFAULT_PERMISSIONS
    assert action_policy.default_mode("knowledge.create") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("knowledge.update") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("knowledge.delete") == action_policy.APPROVAL_REQUIRED
    assert action_policy.SAFE_AUTOMATIC not in action_policy.allowed_modes("knowledge.delete")

    request = pairing.create_pairing_request(
        "vp3",
        "VP3",
        ["tools.execute", "knowledge.search", "knowledge.write"],
    )
    approved = pairing.approve_pairing_request(request["request_id"])
    assert approved is not None
    with db() as connection:
        app = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key='vp3' LIMIT 1"
        ).fetchone()
    assert app is not None
    app_id = int(app["id"])

    tool_items = {
        item["key"]: item
        for item in tools.list_tools(
            {"tools.execute", "knowledge.search", "knowledge.write"}
        )
    }
    for key in ("knowledge.search", "knowledge.create", "knowledge.update", "knowledge.delete"):
        assert key in tool_items
        assert tool_items[key]["available"] is True

    read_only_items = {
        item["key"]: item
        for item in tools.list_tools({"tools.execute", "knowledge.search"})
    }
    assert read_only_items["knowledge.search"]["available"] is True
    assert read_only_items["knowledge.create"]["available"] is False
    assert "knowledge.write" in read_only_items["knowledge.create"]["missing_permissions"]

    create_request = approvals.create_knowledge_create_request(
        "app:vp3",
        {
            "mutation_id": "knowledge-create-001",
            "collection_key": "general",
            "title": "Phoenix launch playbook",
            "content": "Private operating checklist for the Phoenix launch. Never expose raw text in approval metadata.",
            "kind": "note",
        },
    )
    request_id = str(create_request["result"]["request_id"])
    assert create_request["result"]["status"] == "pending"
    with db() as connection:
        queued = connection.execute(
            "SELECT action_key,arguments_json,arguments_meta_json FROM action_requests WHERE id=?",
            (request_id,),
        ).fetchone()
    assert queued is not None
    assert queued["action_key"] == "knowledge.create"
    assert "Phoenix launch playbook" in queued["arguments_json"]
    assert "Private operating checklist" in queued["arguments_json"]
    assert "Phoenix launch playbook" not in queued["arguments_meta_json"]
    assert "Private operating checklist" not in queued["arguments_meta_json"]
    assert '"title_length"' in queued["arguments_meta_json"]
    assert '"content_length"' in queued["arguments_meta_json"]

    executed = approvals.approve_request(request_id)
    assert executed["status"] == "executed"

    identity = {
        "id": app_id,
        "app_key": "vp3",
        "permissions": ["tools.execute", "knowledge.search", "knowledge.write"],
        "scope": {},
    }
    result = knowledge_collection_policy.scoped_search(identity, "Phoenix", limit=10)
    assert result["count"] >= 1
    row = next(item for item in result["items"] if item["title"] == "Phoenix launch playbook")
    canonical = str(row["canonical_id"])
    revision = str(row["record_revision"])
    assert row["authority_source"] == "homeserver"
    assert row["authority_key"].startswith("knowledge_item:")
    assert canonical == federated_data.canonical_id(
        "homeserver", "knowledge", row["authority_key"]
    )
    assert canonical.startswith("fd24_")
    assert len(revision) == 64
    assert row["federation_version"] == "2.4"
    assert row["mirror_only"] is False
    assert row["read_only"] is False
    assert row["mutation_route"] == "homeserver_governed"
    assert row["allowed_mutations"] == ["update", "delete"]
    assert row["citation"]["uri"].startswith("homeserver://knowledge/")
    assert "source_path" not in row
    assert "absolute_path" not in row
    assert result["privacy"]["absolute_paths_exposed"] is False
    assert result["privacy"]["full_documents_returned"] is False

    duplicate_request = approvals.create_knowledge_create_request(
        "app:vp3",
        {
            "mutation_id": "knowledge-create-001",
            "collection_key": "general",
            "title": "Phoenix launch playbook",
            "content": "Private operating checklist for the Phoenix launch. Never expose raw text in approval metadata.",
            "kind": "note",
        },
    )
    duplicate = approvals.approve_request(str(duplicate_request["result"]["request_id"]))
    assert duplicate["status"] == "executed"
    duplicate_search = knowledge_collection_policy.scoped_search(identity, "Phoenix", limit=20)
    assert len([item for item in duplicate_search["items"] if item["title"] == "Phoenix launch playbook"]) == 1

    update_request = approvals.create_knowledge_update_request(
        "app:vp3",
        {
            "canonical_id": canonical,
            "mutation_id": "knowledge-update-001",
            "expected_revision": revision,
            "title": "Phoenix launch operating playbook",
            "content": "Updated private checklist for the Phoenix launch.",
            "kind": "reference",
        },
    )
    updated_exec = approvals.approve_request(str(update_request["result"]["request_id"]))
    assert updated_exec["status"] == "executed"
    updated = knowledge.get_federated_knowledge_by_canonical(canonical)
    assert updated is not None
    assert updated["title"] == "Phoenix launch operating playbook"
    assert updated["kind"] == "reference"
    updated_revision = str(updated["record_revision"])
    assert updated_revision != revision

    stale_request = approvals.create_knowledge_update_request(
        "app:vp3",
        {
            "canonical_id": canonical,
            "mutation_id": "knowledge-update-stale-001",
            "expected_revision": revision,
            "title": "Stale overwrite must fail",
        },
    )
    try:
        approvals.approve_request(str(stale_request["result"]["request_id"]))
        raise AssertionError("stale Knowledge revision was accepted")
    except approvals.ApprovalError as exc:
        assert exc.status_code == 409
    still_current = knowledge.get_federated_knowledge_by_canonical(canonical)
    assert still_current is not None
    assert still_current["title"] == "Phoenix launch operating playbook"

    source_managed = knowledge.create_knowledge_item(
        "Watched source projection",
        "watched_document",
        "This represents source-managed local content.",
        "watched/source.txt",
    )
    source_item = knowledge.get_federated_knowledge_item(int(source_managed["id"]))
    assert source_item is not None
    assert source_item["read_only"] is True
    assert source_item["mutation_route"] == "source_managed_read_only"
    try:
        approvals.create_knowledge_update_request(
            "app:vp3",
            {
                "canonical_id": source_item["canonical_id"],
                "mutation_id": "watched-update-001",
                "expected_revision": source_item["record_revision"],
                "title": "Must not mutate source-managed item",
            },
        )
        raise AssertionError("source-managed Knowledge mutation proposal was accepted")
    except approvals.ApprovalError as exc:
        assert exc.status_code == 409

    cloud_item = federated_data.envelope(
        "vp3_cloud",
        "knowledge",
        "knowledge:777",
        title="Cloud Knowledge",
        content="Cloud authoritative",
    )
    federated_data.observe(cloud_item, observed_source="homeserver")
    try:
        knowledge.update_federated_knowledge(
            cloud_item["canonical_id"],
            {
                "mutation_id": "cloud-knowledge-block-001",
                "expected_revision": cloud_item["record_revision"],
                "title": "Must not cross authority",
            },
            source_app_key="app:vp3",
        )
        raise AssertionError("Cloud-authoritative Knowledge canonical ID was accepted locally")
    except knowledge.FederatedKnowledgeError as exc:
        assert exc.status_code == 404

    allowed = knowledge_collections.create_collection(
        "approved-notes", "Approved Notes", "Scoped app collection"
    )
    assert allowed["collection_key"] == "approved-notes"
    knowledge_collections.set_app_collection_scope(app_id, ["approved-notes"])
    try:
        approvals.create_knowledge_create_request(
            "app:vp3",
            {
                "mutation_id": "scope-block-001",
                "collection_key": "general",
                "title": "Outside collection scope",
                "content": "Must be rejected before approval.",
                "kind": "note",
            },
        )
        raise AssertionError("out-of-scope Knowledge collection proposal was accepted")
    except approvals.ApprovalError as exc:
        assert exc.status_code == 403

    knowledge_collections.set_app_collection_scope(app_id, [])
    with db() as connection:
        connection.execute(
            """
            INSERT INTO app_capability_scopes(
                paired_app_id,cloud_allowed,memory_key_prefixes,knowledge_kinds,tool_names,plugin_keys
            ) VALUES (?,1,'[]','["summary"]','[]','[]')
            ON CONFLICT(paired_app_id) DO UPDATE SET knowledge_kinds=excluded.knowledge_kinds
            """,
            (app_id,),
        )
    try:
        approvals.create_knowledge_create_request(
            "app:vp3",
            {
                "mutation_id": "kind-scope-block-001",
                "collection_key": "general",
                "title": "Wrong kind",
                "content": "Must be rejected before approval.",
                "kind": "note",
            },
        )
        raise AssertionError("out-of-scope Knowledge kind proposal was accepted")
    except approvals.ApprovalError as exc:
        assert exc.status_code == 403

    with db() as connection:
        connection.execute(
            "UPDATE app_capability_scopes SET knowledge_kinds='[]' WHERE paired_app_id=?",
            (app_id,),
        )

    delete_request = approvals.create_knowledge_delete_request(
        "app:vp3",
        {
            "canonical_id": canonical,
            "mutation_id": "knowledge-delete-001",
            "expected_revision": updated_revision,
        },
    )
    deleted_exec = approvals.approve_request(str(delete_request["result"]["request_id"]))
    assert deleted_exec["status"] == "executed"
    assert knowledge.get_federated_knowledge_by_canonical(canonical) is None
    tombstone_key = federated_data.resolve_authority_key(
        canonical,
        authority_source="homeserver",
        dataset="knowledge",
        observed_source="homeserver",
        include_tombstoned=True,
    )
    assert tombstone_key is not None and tombstone_key.startswith("knowledge_item:")

    with db() as connection:
        action_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='action_requests'"
        ).fetchone()[0]
        for action_key in ("knowledge.create", "knowledge.update", "knowledge.delete"):
            assert f"'{action_key}'" in action_schema
        policies = {
            row["tool_key"]: bool(row["enabled"])
            for row in connection.execute(
                "SELECT tool_key,enabled FROM tool_policies"
            ).fetchall()
        }
    for action_key in ("knowledge.create", "knowledge.update", "knowledge.delete"):
        assert policies[action_key] is True

print("HomeServer v2.4 Section 3 Knowledge continuity: PASS")

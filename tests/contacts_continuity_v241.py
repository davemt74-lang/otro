from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v241-contacts-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import action_policy, approvals, contacts, federated_data, pairing, tools  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == list(range(1, 40))

    migration = (ROOT / "database" / "migrations" / "033_governed_contact_actions.sql").read_text(encoding="utf-8")
    migration_sql = "\n".join(
        line for line in migration.splitlines()
        if not line.lstrip().startswith("--")
    )
    assert "contacts.create" in migration_sql
    assert "contacts.update" in migration_sql
    assert "contacts.delete" in migration_sql
    assert "app_permissions" not in migration_sql
    assert "contacts.write" not in migration_sql

    contacts_api = (ROOT / "app" / "contacts_api.py").read_text(encoding="utf-8")
    assert '@router.post("/api/v1/contacts")' not in contacts_api
    assert '@router.put("/api/v1/contacts/' not in contacts_api
    assert '@router.delete("/api/v1/contacts/' not in contacts_api

    remote_bridge = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
    assert 'if op == "contacts.create"' not in remote_bridge
    assert 'if op == "contacts.update"' not in remote_bridge
    assert 'if op == "contacts.delete"' not in remote_bridge

    assert "contacts.write" in pairing.DEFAULT_PERMISSIONS
    assert action_policy.default_mode("contacts.create") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("contacts.update") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("contacts.delete") == action_policy.APPROVAL_REQUIRED
    assert action_policy.SAFE_AUTOMATIC not in action_policy.allowed_modes("contacts.delete")

    tool_items = {
        item["key"]: item
        for item in tools.list_tools(
            {"tools.execute", "contacts.read", "contacts.write"}
        )
    }
    for key in ("contacts.search", "contacts.create", "contacts.update", "contacts.delete"):
        assert key in tool_items
        assert tool_items[key]["available"] is True

    read_only_items = {
        item["key"]: item
        for item in tools.list_tools({"tools.execute", "contacts.read"})
    }
    assert read_only_items["contacts.search"]["available"] is True
    assert read_only_items["contacts.create"]["available"] is False
    assert "contacts.write" in read_only_items["contacts.create"]["missing_permissions"]

    seed = contacts.create_federated_contact({
        "mutation_id": "seed-contact-ada-001",
        "display_name": "Ada Lovelace",
        "organization": "Analytical Engines",
        "email": "ada@example.test",
        "relationship": "research",
        "notes": "Local HomeServer contact.",
    })
    seed_canonical = str(seed["canonical_id"])
    assert seed["authority_source"] == "homeserver"
    assert seed["contact_class"] == "address_book"
    assert seed["authority_key"].startswith("address_book:")
    assert seed_canonical == federated_data.canonical_id(
        "homeserver", "contacts", seed["authority_key"]
    )

    found = contacts.list_federated_contacts("Ada", 20)
    assert len(found) == 1
    assert found[0]["canonical_id"] == seed_canonical

    cloud_contact = federated_data.envelope(
        "vp3_cloud",
        "contacts",
        "core_crm:77",
        title="Cloud CRM Contact",
        content="Cloud authoritative",
    )
    federated_data.observe(cloud_contact, observed_source="homeserver")
    try:
        contacts.update_federated_contact(
            cloud_contact["canonical_id"],
            {
                "mutation_id": "cloud-write-block-001",
                "expected_revision": cloud_contact["record_revision"],
                "display_name": "Must not write",
            },
            source_app_key="app:vp3",
        )
        raise AssertionError("Cloud canonical ID was accepted by HomeServer mutation path")
    except contacts.ContactError as exc:
        assert exc.status_code == 404

    create_request = approvals.create_contact_create_request(
        "app:vp3",
        {
            "mutation_id": "grace-create-001",
            "display_name": "Grace Hopper",
            "email": "grace@example.test",
            "organization": "US Navy",
            "notes": "Private notes must not enter audit metadata.",
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
    assert queued["action_key"] == "contacts.create"
    assert "grace@example.test" in queued["arguments_json"]
    assert "Grace Hopper" in queued["arguments_json"]
    assert "grace@example.test" not in queued["arguments_meta_json"]
    assert "Grace Hopper" not in queued["arguments_meta_json"]
    assert "Private notes" not in queued["arguments_meta_json"]

    approved_create = approvals.approve_request(request_id)
    assert approved_create["status"] == "executed"
    grace = contacts.list_federated_contacts("Grace Hopper", 20)
    assert len(grace) == 1
    grace_canonical = str(grace[0]["canonical_id"])
    grace_revision = str(grace[0]["record_revision"])

    duplicate_create = approvals.create_contact_create_request(
        "app:vp3",
        {
            "mutation_id": "grace-create-001",
            "display_name": "Grace Hopper",
            "email": "grace@example.test",
            "organization": "US Navy",
            "notes": "Private notes must not enter audit metadata.",
        },
    )
    duplicate_approved = approvals.approve_request(str(duplicate_create["result"]["request_id"]))
    assert duplicate_approved["status"] == "executed"
    assert len(contacts.list_federated_contacts("Grace Hopper", 20)) == 1

    update_request = approvals.create_contact_update_request(
        "app:vp3",
        {
            "canonical_id": grace_canonical,
            "mutation_id": "grace-update-001",
            "expected_revision": grace_revision,
            "organization": "Compiler Pioneer",
            "relationship": "colleague",
        },
    )
    approved_update = approvals.approve_request(
        str(update_request["result"]["request_id"])
    )
    assert approved_update["status"] == "executed"
    updated = contacts.get_federated_contact_by_canonical(grace_canonical)
    assert updated is not None
    assert updated["organization"] == "Compiler Pioneer"
    assert updated["relationship"] == "colleague"
    updated_revision = str(updated["record_revision"])
    assert updated_revision != grace_revision

    stale_request = approvals.create_contact_update_request(
        "app:vp3",
        {
            "canonical_id": grace_canonical,
            "mutation_id": "grace-update-stale-001",
            "expected_revision": grace_revision,
            "organization": "Stale overwrite",
        },
    )
    try:
        approvals.approve_request(str(stale_request["result"]["request_id"]))
        raise AssertionError("stale contact revision was accepted")
    except approvals.ApprovalError as exc:
        assert exc.status_code == 409
    still_current = contacts.get_federated_contact_by_canonical(grace_canonical)
    assert still_current is not None
    assert still_current["organization"] == "Compiler Pioneer"

    delete_request = approvals.create_contact_delete_request(
        "app:vp3",
        {
            "canonical_id": grace_canonical,
            "mutation_id": "grace-delete-001",
            "expected_revision": updated_revision,
        },
    )
    approved_delete = approvals.approve_request(
        str(delete_request["result"]["request_id"])
    )
    assert approved_delete["status"] == "executed"
    assert contacts.get_federated_contact_by_canonical(grace_canonical) is None
    tombstone_key = federated_data.resolve_authority_key(
        grace_canonical,
        authority_source="homeserver",
        dataset="contacts",
        observed_source="homeserver",
        include_tombstoned=True,
    )
    assert tombstone_key is not None and tombstone_key.startswith("address_book:")

    with db() as connection:
        action_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='action_requests'"
        ).fetchone()[0]
        for action_key in ("contacts.create", "contacts.update", "contacts.delete"):
            assert f"'{action_key}'" in action_schema
        policies = {
            row["tool_key"]: bool(row["enabled"])
            for row in connection.execute(
                "SELECT tool_key,enabled FROM tool_policies"
            ).fetchall()
        }
    for tool_key in ("contacts.create", "contacts.update", "contacts.delete"):
        assert policies[tool_key] is True

print("HomeServer v2.4 Section 2 contacts continuity: PASS")

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
    assert versions == list(range(1, 34))

    migration = (ROOT / "database" / "migrations" / "033_governed_contact_actions.sql").read_text(encoding="utf-8")
    assert "contacts.create" in migration
    assert "contacts.update" in migration
    assert "contacts.delete" in migration
    assert "app_permissions" not in migration
    assert "contacts.write" not in migration

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
            cloud_contact["canonical_id"], {"display_name": "Must not write"}
        )
        raise AssertionError("Cloud canonical ID was accepted by HomeServer mutation path")
    except contacts.ContactError as exc:
        assert exc.status_code == 404

    create_request = approvals.create_contact_create_request(
        "app:vp3",
        {
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

    update_request = approvals.create_contact_update_request(
        "app:vp3",
        {
            "canonical_id": grace_canonical,
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

    delete_request = approvals.create_contact_delete_request(
        "app:vp3",
        {"canonical_id": grace_canonical},
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

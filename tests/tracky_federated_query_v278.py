from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOME = "11111111-1111-4111-8111-111111111111"
OFFICE = "22222222-2222-4222-8222-222222222222"
NODE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NODE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

with tempfile.TemporaryDirectory(prefix="tracky-v278-s8-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        tracky_federated_query,
        tracky_federated_world,
        tracky_federation_policy,
        tracky_federation_sync,
        tracky_physical_context,
        tracky_site_topology,
    )

    initialize_database()

    assert tracky_federated_query._timestamp_ms("2026-09-27T20:00:00+00:00") > 1_700_000_000_000
    assert tracky_federated_query._timestamp_ms("2002") == 2002

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 61))
        for table in ("tracky_federated_world_history", "tracky_federated_query_audit"):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None

    for site_id, label, node in (
        (HOME, "Home", NODE_A),
        (OFFICE, "Office", NODE_B),
    ):
        tracky_site_topology.register_site(site_id=site_id, label=label)
        tracky_site_topology.register_device(
            device_id=node,
            label=f"{label} Node",
            site_id=site_id,
            hardware_profile="Node",
            trust_state="trusted",
            roles=["site_authority", "persistence", "perception"],
            capabilities={
                "site_authority_eligible": True,
                "physical_context": True,
                "reconciliation": True,
            },
        )
        tracky_site_topology.claim_site_authority(site_id=site_id, device_id=node)

    tracky_site_topology.upsert_relationship(
        subject_id=HOME, relation_type="peers_with", object_id=OFFICE
    )
    tracky_federation_sync.set_local_site_id(HOME)

    def fragment(site: str, node: str, revision: int, label: str, room: str) -> dict:
        entities = [
            {
                "local_id": f"object:{label}",
                "type": "object",
                "label": label.title(),
                "state": "observed" if revision == 1 else "last-known",
                "confidence": .9,
                "observed_at": 1000 + revision * 100,
            },
            {
                "local_id": f"room:{room}",
                "type": "room",
                "label": room.title(),
                "state": "user-confirmed",
                "confidence": 1,
                "observed_at": 1000 + revision * 100,
            },
        ]
        relations = [{
            "subject_local_id": f"object:{label}",
            "predicate": "located_in" if revision == 1 else "located_on",
            "object_local_id": f"room:{room}",
            "confidence": .9,
            "temporal_state": "current",
            "source_event_id": f"event-{site}-{revision}",
            "sequence": revision,
            "as_of": 1000 + revision * 100,
        }]
        if site == OFFICE:
            entities.append({
                "local_id": "person:dave",
                "type": "person",
                "label": "Dave",
                "state": "observed",
                "confidence": .99,
                "observed_at": 1000 + revision * 100,
            })
            relations.append({
                "subject_local_id": "person:dave",
                "predicate": "present_in",
                "object_local_id": f"room:{room}",
                "confidence": .99,
                "temporal_state": "current",
                "source_event_id": f"event-person-{revision}",
                "sequence": revision + 10,
                "as_of": 1000 + revision * 100,
            })
        return {
            "protocol": "physical_federated_world.v1",
            "schema_version": 1,
            "site_id": site,
            "authority_device_id": node,
            "authority_epoch": 1,
            "topology_revision": 10,
            "revision": revision,
            "observed_at": str(1000 + revision * 100),
            "entities": entities,
            "relations": relations,
            "context": {"recent_changes": [f"{label} changed", "person private"]},
            "semantic_only": True,
            "identity_scope": "site_local",
        }

    def ingest(site: str, node: str, revision: int, label: str, room: str) -> None:
        result = tracky_federated_world.ingest_projection({
            "protocol": "physical_federated_world.v1",
            "schema_version": 1,
            "identity_scope": "site_local",
            "cross_site_identity_links": [],
            "semantic_only": True,
            "sites": [fragment(site, node, revision, label, room)],
        }, source="section8-test")
        assert result["changed"] == 1

    ingest(HOME, NODE_A, 1, "keys", "kitchen")
    ingest(OFFICE, NODE_B, 1, "laptop", "desk")
    ingest(OFFICE, NODE_B, 2, "laptop", "shelf")

    with db() as connection:
        history_rows = int(connection.execute(
            "SELECT COUNT(*) FROM tracky_federated_world_history"
        ).fetchone()[0])
    assert history_rows == 3

    local = tracky_federated_query.execute_query({
        "intent": "where_is",
        "site_id": HOME,
        "target_ref": f"site:{HOME}::object%3Akeys",
    })
    assert local["status"] == "ok"
    assert local["results"][0]["data"]["location"]["object_local_id"] == "room:kitchen"

    denied = tracky_federated_query.execute_query({
        "intent": "current_state",
        "site_id": OFFICE,
    })
    assert denied["status"] == "denied"
    assert denied["denied"][0]["reason"] == "source_site_policy_missing"

    office_world_only = {
        "protocol": "physical_federation_policy_relay.v1",
        "schema_version": 1,
        "destination_site_id": HOME,
        "projections": [{
            "protocol": "physical_federation_policy.v1",
            "schema_version": 1,
            "revision": 1,
            "revocation_epoch": 0,
            "governing_site_id": OFFICE,
            "governing_authority_device_id": NODE_B,
            "governing_authority_epoch": 1,
            "sites": [{
                "site_id": OFFICE,
                "revision": 1,
                "mode": "team",
                "allow_federation": True,
                "allow_remote_observation": False,
                "default_identity_visibility": "none",
                "allowed_peer_sites": [HOME],
            }],
            "grants": [{
                "source_site_id": OFFICE,
                "destination_site_id": HOME,
                "scope": "semantic_world_read",
                "status": "granted",
                "revision": 1,
                "reason": "query_current",
            }],
            "consents": [],
            "revocations": [],
        }],
    }
    assert tracky_federation_policy.ingest_cloud_mirror(office_world_only)["accepted"] is True

    current = tracky_federated_query.execute_query({
        "intent": "current_state",
        "site_id": OFFICE,
    })
    assert current["status"] == "ok"
    remote_fragment = current["results"][0]["data"]["fragment"]
    assert not [item for item in remote_fragment["entities"] if item["type"] == "person"]
    assert remote_fragment["context"] == {}

    history_denied = tracky_federated_query.execute_query({
        "intent": "history",
        "site_id": OFFICE,
        "target_ref": f"site:{OFFICE}::object%3Alaptop",
    })
    assert history_denied["status"] == "denied"
    assert history_denied["denied"][0]["reason"] == "permission_not_granted"

    office_history = {
        **office_world_only,
        "projections": [{
            **office_world_only["projections"][0],
            "revision": 2,
            "sites": [{
                **office_world_only["projections"][0]["sites"][0],
                "revision": 2,
            }],
            "grants": [
                {
                    "source_site_id": OFFICE,
                    "destination_site_id": HOME,
                    "scope": "semantic_world_read",
                    "status": "granted",
                    "revision": 2,
                    "reason": "query_current",
                },
                {
                    "source_site_id": OFFICE,
                    "destination_site_id": HOME,
                    "scope": "history_query",
                    "status": "granted",
                    "revision": 1,
                    "reason": "query_history",
                },
            ],
        }],
    }
    assert tracky_federation_policy.ingest_cloud_mirror(office_history)["accepted"] is True

    history = tracky_federated_query.execute_query({
        "intent": "history",
        "site_id": OFFICE,
        "target_ref": f"site:{OFFICE}::object%3Alaptop",
        "limit": 10,
    })
    assert history["status"] == "ok"
    snapshots = history["results"][0]["data"]["snapshots"]
    assert [item["world_revision"] for item in snapshots] == [1, 2]
    assert all(item["entity"]["type"] == "object" for item in snapshots)

    person = tracky_federated_query.execute_query({
        "intent": "last_seen",
        "site_id": OFFICE,
        "target_ref": f"site:{OFFICE}::person%3Adave",
    })
    assert person["status"] == "denied"
    assert person["denied"][0]["reason"] == "person_query_requires_identity_continuity"

    changed = tracky_federated_query.execute_query({
        "intent": "what_changed",
        "site_id": OFFICE,
    })
    assert changed["status"] == "ok"
    diff = changed["results"][0]["data"]["changes"]
    assert any(item["local_id"] == "object:laptop" for item in diff["changed"])
    assert "person:dave" not in str(diff)

    office_revoke_history = {
        **office_history,
        "projections": [{
            **office_history["projections"][0],
            "revision": 3,
            "revocation_epoch": 1,
            "sites": [{
                **office_history["projections"][0]["sites"][0],
                "revision": 3,
            }],
            "grants": [
                {
                    "source_site_id": OFFICE,
                    "destination_site_id": HOME,
                    "scope": "semantic_world_read",
                    "status": "granted",
                    "revision": 3,
                    "reason": "query_current",
                },
                {
                    "source_site_id": OFFICE,
                    "destination_site_id": HOME,
                    "scope": "history_query",
                    "status": "revoked",
                    "revision": 2,
                    "reason": "history_revoked",
                },
            ],
            "revocations": [{
                "revocation_key": f"grant:{OFFICE}|{HOME}|history_query",
                "governing_site_id": OFFICE,
                "revision": 2,
                "revocation_epoch": 1,
                "reason": "history_revoked",
            }],
        }],
    }
    assert tracky_federation_policy.ingest_cloud_mirror(office_revoke_history)["accepted"] is True

    hidden_history = tracky_federated_query.execute_query({
        "intent": "history",
        "site_id": OFFICE,
        "target_ref": f"site:{OFFICE}::object%3Alaptop",
    })
    assert hidden_history["status"] == "denied"
    assert hidden_history["denied"][0]["reason"] == "permission_revoked"

    current_after_history_revoke = tracky_federated_query.execute_query({
        "intent": "current_state",
        "site_id": OFFICE,
    })
    assert current_after_history_revoke["status"] == "ok"

    audit = tracky_federated_query.recent_query_audit(20)
    assert audit["queries"]
    assert len(audit["queries"]) <= 20
    assert all("result_fingerprint" in item for item in audit["queries"])

    capability = tracky_federated_query.public_capability()
    assert capability["protocol"] == "physical_federated_query.v1"
    assert capability["history_scope"] == "history_query"
    assert capability["deny_by_default"] is True
    assert capability["authority_mutation"] is False

    physical_cap = tracky_physical_context.public_capability()
    assert physical_cap["federated_query"]["protocol"] == "physical_federated_query.v1"

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/federated-query"' in api
    assert '"/api/v1/tracky/federated-query/audit"' in api
    assert '@router.post("/api/v1/tracky/federated-query' not in api

print("Tracky V2.78 Section 8 OTRO federated query and history integration: PASS")

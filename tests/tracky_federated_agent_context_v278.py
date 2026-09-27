from __future__ import annotations

import copy
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
PERSON = "44444444-4444-4444-8444-444444444444"
LINK = "66666666-6666-4666-8666-666666666666"
HOME_DAVE = f"site:{HOME}::person%3Adave"
OFFICE_DAVE = f"site:{OFFICE}::person%3Adave"
NOW = 2_000_000

with tempfile.TemporaryDirectory(prefix="tracky-v278-s6-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        federated_data,
        tracky_federated_agent_context,
        tracky_federated_world,
        tracky_federation_policy,
        tracky_federation_sync,
        tracky_identity_continuity,
        tracky_mobile_transition,
        tracky_physical_context,
        tracky_site_topology,
    )

    initialize_database()

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 51))
        for table in (
            "tracky_federated_agent_context",
            "tracky_federated_agent_context_history",
        ):
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
            capabilities={"site_authority_eligible": True, "physical_context": True, "reconciliation": True},
        )
        tracky_site_topology.claim_site_authority(site_id=site_id, device_id=node)

    tracky_site_topology.upsert_relationship(subject_id=HOME, relation_type="peers_with", object_id=OFFICE)
    tracky_federation_sync.set_local_site_id(HOME)

    # Section 7 requires an explicit source-site grant before remote world
    # revisions may contribute to Agent context. The world channel remains
    # non-person, so only revision/freshness—not person-derived context—crosses.
    office_policy = tracky_federation_policy.ingest_cloud_mirror({
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
                "reason": "section6_agent_context_test",
            }],
            "consents": [],
            "revocations": [],
            "semantic_only": True,
            "summary_only": True,
            "authority_assignment": "local_site_policy",
            "cloud_role": "mirror_relay_enforcer",
            "cloud_can_grant": False,
            "cloud_can_revoke": False,
            "cloud_can_change_consent": False,
            "raw_perception": False,
        }],
    })
    assert office_policy["accepted"] is True

    def fragment(site_id: str, node: str, revision: int, observed: int, confidence: float, recent: str):
        return {
            "protocol": "physical_federated_world.v1",
            "schema_version": 1,
            "site_id": site_id,
            "authority_device_id": node,
            "authority_epoch": 1,
            "topology_revision": 10,
            "revision": revision,
            "observed_at": observed,
            "entities": [{
                "local_id": "person:dave",
                "type": "person",
                "label": "Dave",
                "state": "observed",
                "confidence": confidence,
                "observed_at": observed,
            }],
            "relations": [{
                "subject_local_id": "person:dave",
                "predicate": "present_in",
                "object_local_id": "room:kitchen" if site_id == HOME else "room:desk",
                "confidence": confidence,
                "temporal_state": "current",
                "source_event_id": f"event-{site_id}-{revision}",
                "sequence": revision,
                "as_of": observed,
            }],
            "context": {"recent_changes": [recent]},
            "semantic_only": True,
            "identity_scope": "site_local",
        }

    world = {
        "protocol": "physical_federated_world.v1",
        "schema_version": 1,
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "sites": [
            fragment(HOME, NODE_A, 1, NOW - 10_000, .98, "Home door opened"),
            fragment(OFFICE, NODE_B, 1, NOW - 250_000, .90, "Office desk changed"),
        ],
    }
    world_result = tracky_federated_world.ingest_projection(world, source="test")
    assert world_result["changed"] == 2

    identity = {
        "protocol": "physical_identity_continuity.v1",
        "schema_version": 1,
        "identities": [{
            "canonical_identity_id": PERSON,
            "entity_type": "person",
            "status": "active",
            "members": [HOME_DAVE, OFFICE_DAVE],
            "aliases": ["Dave"],
            "revision": 1,
            "created_at": 1000,
            "updated_at": 1200,
        }],
        "links": [{
            "link_id": LINK,
            "canonical_identity_id": PERSON,
            "entity_type": "person",
            "left_ref": HOME_DAVE,
            "right_ref": OFFICE_DAVE,
            "status": "confirmed",
            "reason": "user_confirmed",
            "evidence": [{
                "evidence_id": "evidence-1",
                "type": "user_confirmed",
                "source": "user",
                "source_class": "user",
                "confidence": 1,
                "at": 1200,
                "consent_scope": {},
                "metadata": {},
            }],
            "confidence": 1,
            "auto_confirmed": False,
            "revision": 1,
            "created_at": 1000,
            "updated_at": 1200,
            "confirmed_at": 1200,
        }],
        "blocked_pairs": [],
        "semantic_only": True,
        "cloud_read_only": True,
        "site_local_entities_immutable": True,
        "reversible": True,
    }
    identity_result = tracky_identity_continuity.ingest_projection(
        identity, source="tracky", origin_role="local_governed"
    )
    assert identity_result["changed"] == 1

    first = tracky_federated_agent_context.refresh_context(now_ms=NOW)
    assert first["protocol"] == "physical_federated_agent_context.v1"
    assert first["revision"] == 1
    assert first["agent_state"] == "current"
    assert first["physical_state"] == "present"
    assert first["current_site"]["site_id"] == HOME
    assert first["authority"] == {
        "site_id": HOME,
        "device_id": NODE_A,
        "epoch": 1,
        "basis": "current_site",
    }
    assert first["focus_identity"]["canonical_identity_id"] == PERSON
    assert first["explainability"]["focus_selection"] == "single_active_person"
    assert first["explainability"]["no_location_invention"] is False

    same = tracky_federated_agent_context.refresh_context(now_ms=NOW + 1000)
    assert same["revision"] == 1
    assert same["fingerprint"] == first["fingerprint"]

    # Remote site revision change is retained as an explainable "changed elsewhere" delta.
    office_update = {
        "protocol": "physical_federated_world.v1",
        "schema_version": 1,
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "sites": [fragment(OFFICE, NODE_B, 2, NOW - 5_000, .40, "Office monitor moved")],
    }
    update_result = tracky_federated_world.ingest_projection(office_update, source="test")
    assert update_result["changed"] == 1

    changed = tracky_federated_agent_context.refresh_context(now_ms=NOW + 2000)
    assert changed["revision"] == 2
    assert changed["current_site"]["site_id"] == HOME
    assert changed["changed_elsewhere"][0]["site_id"] == OFFICE
    assert changed["changed_elsewhere"][0]["from_revision"] == 1
    assert changed["changed_elsewhere"][0]["to_revision"] == 2
    assert changed["changed_elsewhere"][0]["recent_changes"] == []

    retained = tracky_federated_agent_context.refresh_context(now_ms=NOW + 3000)
    assert retained["revision"] == 2
    assert retained["changed_elsewhere"][0]["site_id"] == OFFICE

    # Only an explicit continuity subject may bind a mobile transition to this person.
    mobile = {
        "protocol": "physical_mobile_transition.v1",
        "schema_version": 1,
        "identity_linking": False,
        "semantic_only": True,
        "summary_only": True,
        "cloud_read_only": True,
        "authority_assignment": "source_site",
        "person_object_identity_linking": False,
        "temporary_context_site_authority": False,
        "transitions": [{
            "transition_id": "trip-person-1",
            "subject_kind": "explicit_continuity_subject",
            "subject_id": PERSON,
            "subject_scope": "explicit_continuity_subject",
            "source_site_id": HOME,
            "destination_site_id": OFFICE,
            "state": "in_transit",
            "previous_state": "departing",
            "resume_state": "",
            "state_reason": "mobile_motion",
            "confidence": .88,
            "destination_confidence": .60,
            "temporary_context": None,
            "evidence": [{"type": "mobile_motion", "confidence": .9}],
            "revision": 1,
            "identity_linking": False,
            "authority_scope": "source_site",
            "started_at": NOW,
            "state_changed_at": NOW + 4000,
            "updated_at": NOW + 4000,
            "arrived_at": None,
            "canceled_at": None,
            "offline_since": None,
        }],
    }
    mobile_result = tracky_mobile_transition.ingest_projection(
        mobile, source="tracky", origin_role="local_authority"
    )
    assert mobile_result["changed"] == 1

    moving = tracky_federated_agent_context.refresh_context(now_ms=NOW + 5000)
    assert moving["revision"] == 3
    assert moving["physical_state"] == "in_transit"
    assert moving["current_site"] is None
    assert moving["authority"]["site_id"] == HOME
    assert moving["authority"]["basis"] == "active_mobile_transition"
    assert moving["active_mobile_transition"]["subject_id"] == PERSON
    assert moving["explainability"]["mobile_transition_binding"] == "explicit_continuity_subject"
    assert moving["explainability"]["no_location_invention"] is True

    # Clearing the transition and aging evidence across the threshold changes the derived snapshot.
    arrived = copy.deepcopy(mobile)
    arrived["transitions"][0]["state"] = "arrived"
    arrived["transitions"][0]["revision"] = 2
    arrived["transitions"][0]["updated_at"] = NOW + 6000
    arrived["transitions"][0]["arrived_at"] = NOW + 6000
    arrived_result = tracky_mobile_transition.ingest_projection(
        arrived, source="tracky", origin_role="local_authority"
    )
    assert arrived_result["changed"] == 1

    stale = tracky_federated_agent_context.refresh_context(
        now_ms=NOW + 400_000, current_age_ms=120_000, stale_age_ms=300_000
    )
    assert stale["revision"] == 4
    assert stale["agent_state"] == "stale"
    assert stale["current_site"] is None
    assert stale["explainability"]["no_location_invention"] is True

    connected = federated_data.note_peer_connected("vp3_cloud")
    assert connected["needs_reconciliation"] is True
    reconciling = tracky_federated_agent_context.refresh_context(now_ms=NOW + 401_000)
    assert reconciling["agent_state"] == "reconciling"

    disconnected = federated_data.note_peer_disconnected("vp3_cloud", "continuity conflict")
    assert disconnected["needs_reconciliation"] is True
    failed = tracky_federated_agent_context.refresh_context(now_ms=NOW + 402_000)
    assert failed["agent_state"] == "failed"

    cloud = tracky_federated_agent_context.cloud_projection()
    assert cloud["protocol"] == "physical_federated_agent_context.v1"
    assert cloud["summary_only"] is True
    assert cloud["cloud_read_only"] is True
    assert cloud["context_mutation_authority"] is False
    assert cloud["site_authority_mutation"] is False
    assert "age_ms" not in cloud["sites"][0]
    assert "local" not in cloud["sites"][0]

    capability = tracky_federated_agent_context.public_capability()
    assert capability["derived_only"] is True
    assert capability["no_location_invention"] is True
    assert capability["context_mutation_authority"] is False

    physical = tracky_physical_context.current_context()
    assert physical["federated_agent_context"]["protocol"] == "physical_federated_agent_context.v1"

    package = tracky_physical_context._cloud_payload()
    assert package["payload"]["capabilities"]["federated_agent_context"] is True
    assert package["payload"]["capabilities"]["federated_agent_context_protocol"] == "physical_federated_agent_context.v1"
    assert package["payload"]["federated_agent_context"]["cloud_read_only"] is True

    with db() as connection:
        history_count = int(connection.execute(
            "SELECT COUNT(*) FROM tracky_federated_agent_context_history"
        ).fetchone()[0])
        assert 1 <= history_count <= 100

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/federated-agent-context"' in api
    assert '@router.post("/api/v1/tracky/federated-agent-context' not in api

print("Tracky V2.78 Section 6 OTRO federated Agent context integration: PASS")

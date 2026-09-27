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
PERSON = "44444444-4444-4444-8444-444444444444"

with tempfile.TemporaryDirectory(prefix="tracky-v278-s7-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        tracky_federated_world,
        tracky_federation_policy,
        tracky_federation_sync,
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
            "tracky_federation_policy_state",
            "tracky_federation_site_policies",
            "tracky_federation_permissions",
            "tracky_recognition_consents",
            "tracky_federation_policy_revocations",
            "tracky_federation_policy_history",
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

    world = {
        "protocol": "physical_federated_world.v1",
        "schema_version": 1,
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "sites": [{
            "protocol": "physical_federated_world.v1",
            "schema_version": 1,
            "site_id": HOME,
            "authority_device_id": NODE_A,
            "authority_epoch": 1,
            "topology_revision": 10,
            "revision": 1,
            "observed_at": "2026-09-27T20:00:00+00:00",
            "entities": [
                {"local_id": "object:keys", "type": "object", "label": "Keys", "state": "observed", "confidence": .96, "observed_at": 1000},
                {"local_id": "person:dave", "type": "person", "label": "Dave", "state": "observed", "confidence": .98, "observed_at": 1000},
            ],
            "relations": [
                {"subject_local_id": "object:keys", "predicate": "located_in", "object_local_id": "room:kitchen", "confidence": .96, "temporal_state": "current", "source_event_id": "event-keys", "sequence": 1, "as_of": 1000},
                {"subject_local_id": "person:dave", "predicate": "present_in", "object_local_id": "room:kitchen", "confidence": .98, "temporal_state": "current", "source_event_id": "event-dave", "sequence": 2, "as_of": 1000},
            ],
            "context": {"recent_changes": ["Keys moved", "Dave entered kitchen"]},
            "semantic_only": True,
            "identity_scope": "site_local",
        }],
    }
    seeded = tracky_federated_world.ingest_projection(world, source="test")
    assert seeded["changed"] == 1

    # Section 7 is deny-by-default even when topology approves the peer.
    denied = tracky_federation_sync.build_outbound_batch(OFFICE)
    assert denied["destination_site_id"] == OFFICE
    assert denied["envelopes"] == []

    policy = tracky_federation_policy.set_site_policy(
        mode="household",
        allow_federation=True,
        allow_remote_observation=False,
        default_identity_visibility="consented",
        allowed_peer_sites=[OFFICE],
    )
    assert policy["site_id"] == HOME
    assert policy["allow_federation"] is True
    assert policy["allowed_peer_sites"] == [OFFICE]

    still_denied = tracky_federation_sync.build_outbound_batch(OFFICE)
    assert still_denied["envelopes"] == []

    world_grant = tracky_federation_policy.grant_permission(OFFICE, "semantic_world_read")
    assert world_grant["allowed"] is True

    outbound = tracky_federation_sync.build_outbound_batch(OFFICE)
    assert len(outbound["envelopes"]) == 1
    envelope = outbound["envelopes"][0]
    assert envelope["source_site_id"] == HOME
    assert envelope["destination_site_id"] == OFFICE
    assert envelope["policy"]["protocol"] == "physical_federation_policy.v1"
    assert envelope["policy"]["scope"] == "semantic_world_read"
    assert envelope["policy"]["grant_revision"] >= 1
    assert envelope["policy"]["policy_revision"] >= 1
    assert envelope["source_fingerprint"] == envelope["fragment"]["fingerprint"]

    # Direct world relay is permanently non-person; person continuity is a separate consent-gated channel.
    assert {item["local_id"] for item in envelope["fragment"]["entities"]} == {"object:keys"}
    assert not [item for item in envelope["fragment"]["relations"] if item["subject_local_id"] == "person:dave"]

    try:
        tracky_federation_policy.grant_permission(OFFICE, "remote_observation")
        raise AssertionError("remote observation grant bypassed site-level disable")
    except tracky_federation_policy.TrackyFederationPolicyError as exc:
        assert exc.status_code == 403

    tracky_federation_policy.set_site_policy(
        mode="household",
        allow_federation=True,
        allow_remote_observation=True,
        default_identity_visibility="consented",
        allowed_peer_sites=[OFFICE],
    )
    remote_grant = tracky_federation_policy.grant_permission(OFFICE, "remote_observation")
    assert remote_grant["allowed"] is True

    # Recognition/identity consent is site-scoped and revocable.
    before_epoch = tracky_federation_policy.current_report()["revocation_epoch"]
    consent = tracky_federation_policy.set_recognition_consent(
        PERSON, "person_recognition", "granted", source="user"
    )
    assert consent["allowed"] is True
    revoked = tracky_federation_policy.set_recognition_consent(
        PERSON, "person_recognition", "revoked", source="user", reason="user_revoked"
    )
    assert revoked["allowed"] is False
    assert revoked["reason"] == "consent_revoked"
    after_epoch = tracky_federation_policy.current_report()["revocation_epoch"]
    assert after_epoch > before_epoch

    # Revoking the world grant immediately stops outbound federation.
    revoked_world = tracky_federation_policy.revoke_permission(
        OFFICE, "semantic_world_read", reason="privacy_boundary"
    )
    assert revoked_world["allowed"] is False
    assert revoked_world["reason"] == "permission_revoked"
    after_revoke = tracky_federation_sync.build_outbound_batch(OFFICE)
    assert after_revoke["envelopes"] == []

    report = tracky_federation_policy.current_report()
    assert report["protocol"] == "physical_federation_policy.v1"
    assert report["deny_by_default"] is True
    assert report["raw_perception"] is False
    assert report["revocation_epoch"] >= after_epoch
    assert any(item["status"] == "revoked" and item["scope"] == "semantic_world_read" for item in report["grants"])
    assert any(item["scope"] == "person_recognition" and item["status"] == "revoked" for item in report["consents"])
    assert len(report["revocations"]) >= 2

    projection = tracky_federation_policy.cloud_projection(HOME)
    assert projection["governing_site_id"] == HOME
    assert projection["governing_authority_device_id"] == NODE_A
    assert projection["governing_authority_epoch"] == 1
    assert projection["cloud_can_grant"] is False
    assert projection["cloud_can_revoke"] is False
    assert projection["cloud_can_change_consent"] is False
    assert projection["raw_perception"] is False
    assert all(item["source_site_id"] == HOME for item in projection["grants"])
    assert all(item["site_id"] == HOME for item in projection["consents"])

    capability = tracky_federation_policy.public_capability()
    assert capability["deny_by_default"] is True
    assert capability["authority_assignment"] == "local_site_policy"
    assert "semantic_world_read" in capability["permission_scopes"]
    assert "person_recognition" in capability["consent_scopes"]

    physical = tracky_physical_context.current_context()
    assert physical["federation_policy"]["protocol"] == "physical_federation_policy.v1"

    package = tracky_physical_context._cloud_payload()
    assert package["payload"]["capabilities"]["federation_policy"] is True
    assert package["payload"]["capabilities"]["federation_policy_protocol"] == "physical_federation_policy.v1"
    assert package["payload"]["federation_policy"]["governing_site_id"] == HOME
    assert package["payload"]["federation_policy"]["cloud_can_grant"] is False

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    sync_service = (ROOT / "app" / "services" / "tracky_federation_sync.py").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/federation-policy"' in api
    assert '@router.post("/api/v1/tracky/federation-policy' not in api
    assert "filter_world_fragment" in sync_service
    assert "physical_federation_policy.v1" in sync_service

print("Tracky V2.78 Section 7 OTRO federation permissions and consent integration: PASS")

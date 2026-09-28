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
CABIN = "33333333-3333-4333-8333-333333333333"
NODE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NODE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NODE_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

with tempfile.TemporaryDirectory(prefix="tracky-v278-s3-") as data_dir:
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
        versions = [int(row["version"]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions == list(range(1, 53))
        for table in (
            "tracky_federation_identity",
            "tracky_federation_sync_peers",
            "tracky_federation_outbound_ack",
            "tracky_federation_quarantine",
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None

    for site_id, label, node in (
        (HOME, "Home", NODE_A),
        (OFFICE, "Office", NODE_B),
        (CABIN, "Cabin", NODE_C),
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
    tracky_site_topology.upsert_relationship(subject_id=OFFICE, relation_type="bridges_to", object_id=CABIN)
    tracky_federation_sync.set_local_site_id(OFFICE)
    assert tracky_federation_sync.local_site_id() == OFFICE

    def fragment(site_id: str, node: str, epoch: int, revision: int, label: str) -> dict:
        return {
            "protocol": "physical_federated_world.v1",
            "schema_version": 1,
            "site_id": site_id,
            "authority_device_id": node,
            "authority_epoch": epoch,
            "topology_revision": 20,
            "revision": revision,
            "observed_at": "2026-09-27T17:00:00+00:00",
            "entities": [
                {
                    "local_id": f"object:{label}",
                    "type": "object",
                    "label": label,
                    "state": "observed",
                    "confidence": 0.93,
                    "observed_at": 1000 + revision,
                }
            ],
            "relations": [],
            "context": {"summary": f"{label} visible"},
            "semantic_only": True,
            "identity_scope": "site_local",
        }

    def envelope(
        source: str,
        destination: str,
        node: str,
        epoch: int,
        revision: int,
        label: str,
        fingerprint: str,
        *,
        policy_revision: int = 1,
        grant_revision: int = 1,
        revocation_epoch: int = 0,
    ) -> dict:
        return {
            "protocol": "physical_federation_sync.v1",
            "schema_version": 1,
            "envelope_id": f"fed:{source}:{epoch}:{revision}:{fingerprint[:16]}",
            "source_site_id": source,
            "destination_site_id": destination,
            "source_authority_device_id": node,
            "source_authority_epoch": epoch,
            "source_world_revision": revision,
            "source_fingerprint": fingerprint,
            "topology_revision": 999,
            "policy": {
                "protocol": "physical_federation_policy.v1",
                "scope": "semantic_world_read",
                "grant_revision": grant_revision,
                "policy_revision": policy_revision,
                "revocation_epoch": revocation_epoch,
                "world_projection": "non_person_v1",
            },
            "emitted_at": "2026-09-27T17:00:00+00:00",
            "fragment": {
                **fragment(source, node, epoch, revision, label),
                "context": {},
            },
        }

    def mirror_world_policy(source: str, destination: str, node: str, epoch: int, revision: int = 1) -> dict:
        return tracky_federation_policy.ingest_cloud_mirror({
            "protocol": "physical_federation_policy_relay.v1",
            "schema_version": 1,
            "destination_site_id": destination,
            "projections": [{
                "protocol": "physical_federation_policy.v1",
                "schema_version": 1,
                "revision": revision,
                "revocation_epoch": 0,
                "governing_site_id": source,
                "governing_authority_device_id": node,
                "governing_authority_epoch": epoch,
                "sites": [{
                    "site_id": source,
                    "revision": revision,
                    "mode": "team",
                    "allow_federation": True,
                    "allow_remote_observation": False,
                    "default_identity_visibility": "none",
                    "allowed_peer_sites": [destination],
                }],
                "grants": [{
                    "source_site_id": source,
                    "destination_site_id": destination,
                    "scope": "semantic_world_read",
                    "status": "granted",
                    "revision": 1,
                    "reason": "section3_test",
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

    # Seed the local site's own authoritative world.
    local_projection = {
        "protocol": "physical_federated_world.v1",
        "schema_version": 1,
        "sites": [fragment(OFFICE, NODE_B, 1, 3, "desk")],
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "cloud_read_only": True,
        "authority_assignment": "local_only",
    }
    tracky_federated_world.ingest_projection(local_projection, source="local")

    request = tracky_federation_sync.cloud_sync_request()
    assert request["protocol"] == "physical_federation_sync.v1"
    assert request["available"] is True
    assert request["local_site_id"] == OFFICE
    assert request["received_cursors"] == []

    assert mirror_world_policy(HOME, OFFICE, NODE_A, 1)["accepted"] is True
    assert mirror_world_policy(CABIN, OFFICE, NODE_C, 1)["accepted"] is True

    forged_person = envelope(HOME, OFFICE, NODE_A, 1, 5, "person", "9" * 64)
    forged_person["fragment"]["entities"][0]["type"] = "person"
    forged = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [forged_person]}
    )
    assert forged["quarantined"] == 1
    with db() as connection:
        forged_row = connection.execute(
            "SELECT reason FROM tracky_federation_quarantine ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert forged_row["reason"] == "invalid_fragment"

    home_env = envelope(HOME, OFFICE, NODE_A, 1, 7, "keys", "a" * 64)
    first = tracky_federation_sync.ingest_cloud_batch(
        {
            "protocol": "physical_federation_sync.v1",
            "schema_version": 1,
            "destination_site_id": OFFICE,
            "topology_revision": 1,
            "envelopes": [home_env],
        }
    )
    assert first["accepted"] is True
    assert first["applied"] == 1
    assert first["quarantined"] == 0

    report = tracky_federated_world.current_report()
    assert report["site_count"] == 2
    assert {site["site_id"] for site in report["sites"]} == {HOME, OFFICE}

    cursors = tracky_federation_sync.receive_cursors()
    assert cursors == [
        {"site_id": HOME, "revision": 7, "fingerprint": "a" * 64, "authority_epoch": 1}
    ]

    retry = tracky_federation_sync.ingest_cloud_batch(
        {
            "protocol": "physical_federation_sync.v1",
            "destination_site_id": OFFICE,
            "envelopes": [home_env],
        }
    )
    assert retry["idempotent"] == 1 and retry["applied"] == 0

    stale_env = envelope(HOME, OFFICE, NODE_A, 1, 6, "older", "b" * 64)
    stale = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [stale_env]}
    )
    assert stale["stale"] == 1

    conflict_env = envelope(HOME, OFFICE, NODE_A, 1, 7, "wallet", "c" * 64)
    conflict = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [conflict_env]}
    )
    assert conflict["quarantined"] == 1
    with db() as connection:
        conflict_row = connection.execute(
            "SELECT reason FROM tracky_federation_quarantine ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert conflict_row["reason"] == "revision_conflict"

    # A newer source authority epoch is held until local topology catches up.
    future_epoch = envelope(HOME, OFFICE, NODE_A, 2, 8, "keys", "d" * 64)
    held = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [future_epoch]}
    )
    assert held["quarantined"] == 1
    with db() as connection:
        held_row = connection.execute(
            "SELECT reason FROM tracky_federation_quarantine ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert held_row["reason"] == "topology_ahead"

    tracky_site_topology.release_site_authority(site_id=HOME, device_id=NODE_A, reason="epoch_test")
    second_authority = tracky_site_topology.claim_site_authority(site_id=HOME, device_id=NODE_A)
    assert second_authority["authority_epoch"] == 2

    stale_policy = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [future_epoch]}
    )
    assert stale_policy["quarantined"] == 1
    with db() as connection:
        stale_policy_row = connection.execute(
            "SELECT reason FROM tracky_federation_quarantine ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert stale_policy_row["reason"] == "policy_denied"

    refreshed_policy = mirror_world_policy(HOME, OFFICE, NODE_A, 2, revision=2)
    assert refreshed_policy["accepted"] is True
    future_epoch["policy"]["policy_revision"] = 2
    caught_up = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [future_epoch]}
    )
    assert caught_up["applied"] == 1

    # Direct Home <-> Cabin federation is not approved.
    cabin_env = envelope(CABIN, OFFICE, NODE_C, 1, 2, "lamp", "e" * 64)
    cabin = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [cabin_env]}
    )
    assert cabin["applied"] == 1  # Office is explicitly bridged to Cabin.

    unapproved = copy.deepcopy(home_env)
    unapproved["source_site_id"] = CABIN
    unapproved["source_authority_device_id"] = NODE_C
    unapproved["fragment"] = {
        **fragment(CABIN, NODE_C, 1, 9, "lamp"),
        "context": {},
    }
    unapproved["source_world_revision"] = 9
    unapproved["source_fingerprint"] = "f" * 64
    unapproved["destination_site_id"] = HOME
    wrong_destination = tracky_federation_sync.ingest_cloud_batch(
        {"protocol": "physical_federation_sync.v1", "destination_site_id": OFFICE, "envelopes": [unapproved]}
    )
    assert wrong_destination["ignored"] == 1

    # Cached remote sites are never re-originated to Cloud after local identity is pinned.
    package = tracky_physical_context._cloud_payload()
    assert package["payload"]["federation_sync"]["local_site_id"] == OFFICE
    assert package["payload"]["capabilities"]["federation_sync"] is True
    assert package["payload"]["capabilities"]["federation_sync_protocol"] == "physical_federation_sync.v1"
    cloud_world = package["payload"]["federated_world"]
    assert cloud_world["origin_scope"] == "local_site_only"
    assert cloud_world["site_count"] == 1
    assert cloud_world["sites"][0]["site_id"] == OFFICE

    denied_without_policy = tracky_federation_sync.build_outbound_batch(HOME)
    assert denied_without_policy["envelopes"] == []
    tracky_federation_policy.set_site_policy(
        mode="team",
        allow_federation=True,
        allowed_peer_sites=[HOME],
    )
    assert tracky_federation_policy.grant_permission(HOME, "semantic_world_read")["allowed"] is True

    outbound = tracky_federation_sync.build_outbound_batch(HOME)
    assert outbound["destination_site_id"] == HOME
    assert len(outbound["envelopes"]) >= 1
    office_envelopes = [item for item in outbound["envelopes"] if item["source_site_id"] == OFFICE]
    assert office_envelopes and office_envelopes[0]["source_world_revision"] == 3
    assert office_envelopes[0]["policy"]["world_projection"] == "non_person_v1"
    changed = tracky_federation_sync.acknowledge_outbound(
        HOME, [{"site_id": OFFICE, "revision": 3, "fingerprint": office_envelopes[0]["source_fingerprint"]}]
    )
    assert changed == 1
    outbound_after_ack = tracky_federation_sync.build_outbound_batch(HOME)
    assert not [item for item in outbound_after_ack["envelopes"] if item["source_site_id"] == OFFICE]

    status = tracky_federation_sync.status()
    assert status["local_site_id"] == OFFICE
    assert status["cloud_role"] == "relay_only"
    assert status["authority_assignment"] == "local_only"
    assert status["quarantine_open"] >= 2

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "tracky_federation_sync.py").read_text(encoding="utf-8")
    migration = (ROOT / "database" / "migrations" / "046_tracky_federation_sync.sql").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/federation-sync"' in api
    assert '@router.post("/api/v1/tracky/federation-sync' not in api
    assert "cloud_role" in service and "relay_only" in service
    assert "tracky_federation_quarantine" in migration

print("Tracky V2.78 Section 3 OTRO federation sync integration: PASS")

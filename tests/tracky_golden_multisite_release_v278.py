from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "tracky_v278_golden_multisite_scenarios.json").read_text(encoding="utf-8")
)
HOME = FIXTURE["sites"]["home"]["site_id"]
OFFICE = FIXTURE["sites"]["office"]["site_id"]
CABIN = FIXTURE["sites"]["cabin"]["site_id"]
NODE_A = FIXTURE["sites"]["home"]["authority_device_id"]
NODE_B = FIXTURE["sites"]["office"]["authority_device_id"]
NODE_C = FIXTURE["sites"]["cabin"]["authority_device_id"]

required_scenarios = {
    "healthy-two-site-current",
    "transport-partition-stale-query",
    "reconnect-revision-gap-full-snapshot",
    "authority-epoch-change-revalidation",
    "same-revision-fingerprint-conflict",
    "permission-revoked-during-partition",
    "mobile-transition-site-boundary",
    "history-immutable-through-reconciliation",
    "restart-during-reconciliation",
    "three-site-cloud-relay-no-authority",
}
required_invariants = {
    "origin-site-authority-only",
    "cloud-remains-relay-and-mirror-only",
    "semantic-only-federation",
    "cross-site-identity-deny-by-default",
    "stale-data-must-be-labeled",
    "partition-never-promotes-remote-or-cloud-authority",
    "authority-epoch-change-requires-revalidation",
    "same-revision-fingerprint-conflict-fails-closed",
    "revocation-wins-over-cached-permission",
    "reconciliation-state-survives-restart",
    "history-remains-immutable",
    "mobile-transitions-do-not-merge-site-local-identities",
}

assert FIXTURE["format"] == "tracky_v278_golden_multisite_scenarios.v1"
assert FIXTURE["version"] == "2.78"
assert {item["id"] for item in FIXTURE["scenarios"]} == required_scenarios
assert required_invariants.issubset(set(FIXTURE["invariants"]))

with tempfile.TemporaryDirectory(prefix="tracky-v278-s10-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        tracky_federated_agent_context,
        tracky_federated_query,
        tracky_federated_world,
        tracky_federation_policy,
        tracky_federation_reconciliation,
        tracky_federation_sync,
        tracky_identity_continuity,
        tracky_mobile_transition,
        tracky_site_topology,
    )

    initialize_database()
    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 57))

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
    tracky_federation_sync.set_local_site_id(HOME)

    # Golden healthy/current state.
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_federation_sync_peers(
              remote_site_id,status,last_received_revision,last_received_fingerprint,
              last_received_authority_epoch,last_received_at
            ) VALUES (?,'current',4,?,1,CURRENT_TIMESTAMP)
            """,
            (OFFICE, "same"),
        )
    healthy = tracky_federation_reconciliation.note_peer_contact(
        OFFICE, {"revision": 4, "fingerprint": "same", "authority_epoch": 1}
    )
    assert healthy["status"] == "current"

    # Golden partition -> stale/partial.
    partitioned = tracky_federation_reconciliation.mark_partition(OFFICE, "golden_partition")
    assert partitioned["status"] == "partitioned"
    partial = tracky_federation_reconciliation.annotate_query_result({
        "status": "ok",
        "results": [{"site_id": HOME, "data": {}}, {"site_id": OFFICE, "data": {}}],
        "uncertainty": [],
    })
    assert partial["status"] == "partial"
    assert "federation_stale" in partial["uncertainty"]

    # Golden revision gap -> full authoritative reconciliation.
    processed = tracky_federation_reconciliation.process_remote_cursors([
        {"site_id": OFFICE, "revision": 9, "fingerprint": "r9", "authority_epoch": 1}
    ])
    request = processed[0]["reconciliation"]
    assert request and request["request_mode"] == "authoritative_full"
    assert request["authority_assignment"] == "origin_only"

    with db() as connection:
        connection.execute(
            """
            UPDATE tracky_federation_sync_peers
            SET last_received_revision=9,last_received_fingerprint='r9',
                last_received_authority_epoch=1,last_received_at=CURRENT_TIMESTAMP,
                status='current',updated_at=CURRENT_TIMESTAMP
            WHERE remote_site_id=?
            """,
            (OFFICE,),
        )
    completed = tracky_federation_reconciliation.complete_reconciliation(
        OFFICE,
        {"revision": 9, "fingerprint": "r9", "authority_epoch": 1},
        reconciliation_id=request["reconciliation_id"],
    )
    assert completed["status"] == "current"

    # Golden same-revision conflict -> fail closed.
    conflict = tracky_federation_reconciliation.note_peer_contact(
        OFFICE, {"revision": 9, "fingerprint": "different", "authority_epoch": 1}
    )
    assert conflict["status"] == "failed"
    assert conflict["last_error"] == "same_revision_fingerprint_conflict"

    # Capability matrix proves all federation layers remain bounded.
    capabilities = {
        "topology": tracky_site_topology.public_capability(),
        "world": tracky_federated_world.public_capability(),
        "sync": tracky_federation_sync.public_capability(),
        "mobile": tracky_mobile_transition.public_capability(),
        "identity": tracky_identity_continuity.public_capability(),
        "agent_context": tracky_federated_agent_context.public_capability(),
        "policy": tracky_federation_policy.public_capability(),
        "query": tracky_federated_query.public_capability(),
        "reconciliation": tracky_federation_reconciliation.public_capability(),
    }
    assert capabilities["reconciliation"]["cloud_role"] == "relay_and_mirror_only"
    assert capabilities["reconciliation"]["authority_assignment"] == "origin_only"
    assert capabilities["reconciliation"]["same_revision_conflicts"] == "fail_closed"
    assert capabilities["query"]["authority_mutation"] is False
    assert capabilities["identity"]["cloud_can_merge_identities"] is False
    assert capabilities["sync"]["cross_site_identity_linking"] is False
    assert capabilities["sync"]["cloud_role"] == "relay_only"

    # The permanent scenario library must remain represented in CI/release gates.
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "homeserver-v24-release.yml").read_text(encoding="utf-8")
    assert "tracky_golden_multisite_release_v278.py" in ci
    assert "tracky_golden_multisite_release_v278.py" in release

print("Tracky V2.78 Section 10 HomeServer golden multi-site release hardening: PASS")

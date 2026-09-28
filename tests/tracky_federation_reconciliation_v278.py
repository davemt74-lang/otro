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

with tempfile.TemporaryDirectory(prefix="tracky-v278-s9-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        tracky_federation_reconciliation,
        tracky_federation_sync,
        tracky_site_topology,
    )

    initialize_database()
    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 53))
        for table in (
            "tracky_federation_reconciliation_state",
            "tracky_federation_reconciliation_runs",
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None

    for site_id, label, node in ((HOME, "Home", NODE_A), (OFFICE, "Office", NODE_B)):
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

    tracky_site_topology.upsert_relationship(
        subject_id=HOME, relation_type="peers_with", object_id=OFFICE
    )
    tracky_federation_sync.set_local_site_id(OFFICE)

    # Seed a known remote receive cursor at revision 4.
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_federation_sync_peers(
              remote_site_id,status,last_received_revision,last_received_fingerprint,
              last_received_authority_epoch,last_received_at
            ) VALUES (?,'current',4,?,1,CURRENT_TIMESTAMP)
            """,
            (HOME, "a" * 64),
        )

    current = tracky_federation_reconciliation.note_peer_contact(
        HOME, {"revision": 4, "fingerprint": "a" * 64, "authority_epoch": 1}
    )
    assert current["status"] == "current"

    partitioned = tracky_federation_reconciliation.mark_partition(HOME, "simulated_offline")
    assert partitioned["status"] == "partitioned"
    report = tracky_federation_reconciliation.current_report()
    assert report["peers"][0]["status"] == "partitioned"

    # A reconnect that reports a newer full semantic world enters reconciliation.
    processed = tracky_federation_reconciliation.process_remote_cursors([
        {"site_id": HOME, "revision": 9, "fingerprint": "b" * 64, "authority_epoch": 1}
    ])
    assert processed[0]["status"] == "reconciling"
    request = processed[0]["reconciliation"]
    assert request["request_mode"] == "authoritative_full"
    assert request["authority_assignment"] == "origin_only"

    # It cannot become current until the authoritative semantic snapshot has
    # actually advanced the local federation cursor.
    incomplete = tracky_federation_reconciliation.complete_reconciliation(
        HOME,
        {"revision": 9, "fingerprint": "b" * 64, "authority_epoch": 1},
        reconciliation_id=request["reconciliation_id"],
    )
    assert incomplete["status"] == "reconciling"

    with db() as connection:
        connection.execute(
            """
            UPDATE tracky_federation_sync_peers
            SET status='current',last_received_revision=9,last_received_fingerprint=?,
                last_received_authority_epoch=1,last_received_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE remote_site_id=?
            """,
            ("b" * 64, HOME),
        )
    completed = tracky_federation_reconciliation.complete_reconciliation(
        HOME,
        {"revision": 9, "fingerprint": "b" * 64, "authority_epoch": 1},
        reconciliation_id=request["reconciliation_id"],
    )
    assert completed["status"] == "current"
    assert completed["stale_since"] == ""

    # Same revision with a different fingerprint fails closed.
    conflict = tracky_federation_reconciliation.note_peer_contact(
        HOME, {"revision": 9, "fingerprint": "c" * 64, "authority_epoch": 1}
    )
    assert conflict["status"] == "failed"
    assert conflict["last_error"] == "same_revision_fingerprint_conflict"

    # Cloud transport failure never promotes Cloud or a peer; it marks mirrors stale.
    changed = tracky_federation_reconciliation.mark_all_remote_partitioned("cloud_unavailable")
    assert changed == 1
    stale = tracky_federation_reconciliation.current_report()["peers"][0]
    assert stale["status"] == "partitioned"
    assert stale["last_error"] == "cloud_unavailable"

    query = tracky_federation_reconciliation.annotate_query_result({
        "status": "ok",
        "results": [
            {"site_id": OFFICE, "data": {"fragment": {}}},
            {"site_id": HOME, "data": {"fragment": {}}},
        ],
        "uncertainty": [],
    })
    assert query["results"][0]["federation_freshness"]["fresh"] is True
    assert query["results"][1]["federation_freshness"]["fresh"] is False
    assert query["status"] == "partial"
    assert "federation_stale" in query["uncertainty"]

    # Retry scheduling is bounded and fails closed after exhaustion.
    retry = None
    for attempt in range(1, 7):
        retry = tracky_federation_reconciliation.schedule_retry(HOME, f"retry-{attempt}")
    assert retry is not None
    assert retry["status"] == "failed"
    assert retry["retry_count"] == 6
    assert retry["next_retry_at"] == ""

    capability = tracky_federation_reconciliation.public_capability()
    assert capability["protocol"] == "physical_federation_reconciliation.v1"
    assert capability["authority_assignment"] == "origin_only"
    assert capability["cloud_role"] == "relay_and_mirror_only"
    assert capability["same_revision_conflicts"] == "fail_closed"
    assert "partition-never-promotes-remote-or-cloud-authority" in capability["boundaries"]

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/federation-reconciliation"' in api

print("Tracky V2.78 Section 9 OTRO federation failure/partition reconciliation: PASS")

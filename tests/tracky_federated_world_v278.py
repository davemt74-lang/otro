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

with tempfile.TemporaryDirectory(prefix="tracky-v278-s2-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import tracky_federated_world, tracky_physical_context, tracky_site_topology  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions == list(range(1, 51))
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracky_federated_world_fragments'"
        ).fetchone() is not None

    for site_id, label, node in ((HOME, "Home", NODE_A), (OFFICE, "Office", NODE_B)):
        tracky_site_topology.register_site(site_id=site_id, label=label)
        tracky_site_topology.register_device(
            device_id=node, label=f"{label} Node", site_id=site_id, hardware_profile="Node",
            trust_state="trusted", roles=["site_authority", "persistence", "perception"],
            capabilities={"site_authority_eligible": True, "physical_context": True, "reconciliation": True},
        )
        tracky_site_topology.claim_site_authority(site_id=site_id, device_id=node)

    projection = {
        "protocol": "physical_federated_world.v1",
        "schema_version": 1,
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "sites": [
            {
                "protocol": "physical_federated_world.v1",
                "schema_version": 1,
                "site_id": HOME,
                "authority_device_id": NODE_A,
                "authority_epoch": 1,
                "topology_revision": 6,
                "revision": 10,
                "observed_at": "2026-09-27T16:30:00+00:00",
                "entities": [
                    {"local_id": "person:dave", "type": "person", "label": "Dave", "state": "observed", "confidence": 0.98, "observed_at": 1000},
                    {"local_id": "room:office", "type": "room", "label": "Office", "state": "user-confirmed", "confidence": 1.0, "observed_at": 1000},
                ],
                "relations": [
                    {
                        "subject_local_id": "person:dave", "predicate": "located_in", "object_local_id": "room:office",
                        "confidence": 0.98, "temporal_state": "current", "source_event_id": "event-home-10",
                        "sequence": 10, "as_of": 1000, "value": {},
                    }
                ],
                "context": {"current_room": "Office"},
            },
            {
                "protocol": "physical_federated_world.v1",
                "schema_version": 1,
                "site_id": OFFICE,
                "authority_device_id": NODE_B,
                "authority_epoch": 1,
                "topology_revision": 6,
                "revision": 4,
                "observed_at": "2026-09-27T16:30:00+00:00",
                "entities": [
                    {"local_id": "person:dave", "type": "person", "label": "Dave", "state": "observed", "confidence": 0.90, "observed_at": 1000},
                ],
                "relations": [],
                "context": {"current_room": "Lobby"},
            },
        ],
    }

    first = tracky_federated_world.ingest_projection(projection, source="v278-s2-test")
    assert first == {"accepted": True, "sites": 2, "changed": 2, "stale": 0}
    retry = tracky_federated_world.ingest_projection(projection, source="v278-s2-test")
    assert retry["sites"] == 2 and retry["changed"] == 0 and retry["stale"] == 0

    report = tracky_federated_world.current_report()
    assert report["available"] is True
    assert report["site_count"] == 2
    assert report["identity_scope"] == "site_local"
    refs = [
        entity["ref"]
        for site in report["sites"]
        for entity in site["entities"]
        if entity["local_id"] == "person:dave"
    ]
    assert len(refs) == 2 and refs[0] != refs[1]
    assert report["cross_site_identity_links"] == []

    stale = copy.deepcopy(projection)
    stale["sites"] = [copy.deepcopy(projection["sites"][0])]
    stale["sites"][0]["revision"] = 9
    stale_result = tracky_federated_world.ingest_projection(stale, source="v278-s2-test")
    assert stale_result["stale"] == 1 and stale_result["changed"] == 0

    conflict = copy.deepcopy(projection)
    conflict["sites"] = [copy.deepcopy(projection["sites"][0])]
    conflict["sites"][0]["entities"][0]["confidence"] = 0.55
    try:
        tracky_federated_world.ingest_projection(conflict, source="v278-s2-test")
        raise AssertionError("same-revision conflicting world was accepted")
    except tracky_federated_world.TrackyFederatedWorldError as exc:
        assert exc.status_code == 409
        assert "revision conflicts" in str(exc)

    wrong_authority = copy.deepcopy(projection)
    wrong_authority["sites"] = [copy.deepcopy(projection["sites"][0])]
    wrong_authority["sites"][0]["revision"] = 11
    wrong_authority["sites"][0]["authority_device_id"] = NODE_B
    try:
        tracky_federated_world.ingest_projection(wrong_authority, source="v278-s2-test")
        raise AssertionError("wrong site authority was accepted")
    except tracky_federated_world.TrackyFederatedWorldError as exc:
        assert exc.status_code == 409
        assert "authority does not match" in str(exc)

    linked = copy.deepcopy(projection)
    linked["cross_site_identity_links"] = [{"a": "person:dave", "b": "person:dave"}]
    try:
        tracky_federated_world.normalize_projection(linked)
        raise AssertionError("cross-site identity linking was accepted before Section 5")
    except tracky_federated_world.TrackyFederatedWorldError as exc:
        assert "deferred" in str(exc)

    raw = copy.deepcopy(projection)
    raw["sites"][0]["entities"][0]["frame_data"] = "private"
    try:
        tracky_federated_world.normalize_projection(raw)
        raise AssertionError("raw perception entered federated world")
    except tracky_federated_world.TrackyFederatedWorldError as exc:
        assert exc.status_code == 422

    cloud = tracky_federated_world.cloud_projection()
    assert cloud["summary_only"] is True
    assert cloud["cloud_read_only"] is True
    assert cloud["authority_assignment"] == "local_only"
    assert cloud["identity_scope"] == "site_local"
    assert cloud["cross_site_identity_links"] == []
    assert "frame_data" not in str(cloud)

    physical_cap = tracky_physical_context.public_capability()
    assert physical_cap["federated_world"]["protocol"] == "physical_federated_world.v1"
    assert physical_cap["federated_world"]["cross_site_identity_linking"] is False

    # Section 7 keeps durable federation storage intact while deny-by-default
    # hides/transmits no cross-site state until this HomeServer has a resolved local site.
    visible = tracky_physical_context.current_context()["federated_world"]
    assert visible["site_count"] == 0
    assert visible["policy_reason"] == "local_site_unresolved"
    assert tracky_federated_world.current_report()["site_count"] == 2

    package = tracky_physical_context._cloud_payload()
    assert package["payload"]["federated_world"] is None
    assert package["payload"]["mobile_transitions"] is None
    assert package["payload"]["identity_continuity"] is None
    assert package["payload"]["federated_agent_context"] is None
    assert package["payload"]["federation_policy"] is None
    assert package["payload"]["federation_sync"]["available"] is False
    assert package["payload"]["capabilities"]["federated_world"] is True
    assert package["payload"]["capabilities"]["federated_world_protocol"] == "physical_federated_world.v1"

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "tracky_federated_world.py").read_text(encoding="utf-8")
    migration = (ROOT / "database" / "migrations" / "045_tracky_federated_world.sql").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/federated-world"' in api
    assert '@router.post("/api/v1/tracky/federated-world' not in api
    assert "cross_site_identity_linking" in service
    assert "tracky_federated_world_fragments" in migration

print("Tracky V2.78 Section 2 OTRO federated world integration: PASS")

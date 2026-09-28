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
PERSON_CANON = "44444444-4444-4444-8444-444444444444"
DEVICE_CANON = "55555555-5555-4555-8555-555555555555"
PERSON_LINK = "66666666-6666-4666-8666-666666666666"
DEVICE_LINK = "77777777-7777-4777-8777-777777777777"

HOME_DAVE = f"site:{HOME}::person%3Adave"
OFFICE_DAVE = f"site:{OFFICE}::person%3Adave"
CABIN_DAVE = f"site:{CABIN}::person%3Adave"
HOME_POCKET = f"site:{HOME}::device%3Apocket"
CABIN_POCKET = f"site:{CABIN}::device%3Apocket"

with tempfile.TemporaryDirectory(prefix="tracky-v278-s5-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        tracky_federated_world,
        tracky_federation_sync,
        tracky_identity_continuity,
        tracky_physical_context,
        tracky_site_topology,
    )

    initialize_database()

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 56))
        for table in (
            "tracky_canonical_identities",
            "tracky_identity_links",
            "tracky_identity_blocked_pairs",
            "tracky_identity_history",
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
    tracky_site_topology.upsert_relationship(subject_id=HOME, relation_type="peers_with", object_id=CABIN)
    tracky_federation_sync.set_local_site_id(HOME)

    world = {
        "protocol": "physical_federated_world.v1",
        "schema_version": 1,
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "sites": [],
    }
    for site_id, node, label in (
        (HOME, NODE_A, "Home"),
        (OFFICE, NODE_B, "Office"),
        (CABIN, NODE_C, "Cabin"),
    ):
        world["sites"].append({
            "protocol": "physical_federated_world.v1",
            "schema_version": 1,
            "site_id": site_id,
            "authority_device_id": node,
            "authority_epoch": 1,
            "topology_revision": 10,
            "revision": 1,
            "observed_at": "2026-09-27T18:00:00+00:00",
            "entities": [
                {"local_id": "person:dave", "type": "person", "label": "Dave", "state": "observed", "confidence": .97, "observed_at": 1000},
                {"local_id": "device:pocket", "type": "device", "label": "Pocket", "state": "observed", "confidence": 1.0, "observed_at": 1000},
            ],
            "relations": [],
            "context": {"site": label},
            "semantic_only": True,
            "identity_scope": "site_local",
        })
    tracky_federated_world.ingest_projection(world, source="test")

    proposed = {
        "protocol": "physical_identity_continuity.v1",
        "schema_version": 1,
        "identities": [],
        "links": [{
            "link_id": PERSON_LINK,
            "canonical_identity_id": PERSON_CANON,
            "entity_type": "person",
            "left_ref": HOME_DAVE,
            "right_ref": OFFICE_DAVE,
            "status": "proposed",
            "reason": "insufficient_independent_person_evidence",
            "evidence": [{
                "evidence_id": "evidence-1",
                "type": "recognition_match",
                "source": "face-model",
                "source_class": "biometric",
                "confidence": .98,
                "at": 1000,
                "consent_scope": {"identity_linking_allowed": True},
                "metadata": {},
            }],
            "confidence": .98,
            "auto_confirmed": False,
            "revision": 1,
            "created_at": 1000,
            "updated_at": 1100,
            "confirmed_at": None,
            "rejected_at": None,
            "revoked_at": None,
            "split_at": None,
        }],
        "blocked_pairs": [],
        "semantic_only": True,
        "cloud_read_only": True,
        "site_local_entities_immutable": True,
        "reversible": True,
    }
    first = tracky_identity_continuity.ingest_projection(
        proposed, source="tracky", origin_role="local_governed"
    )
    assert first["changed"] == 1
    report = tracky_identity_continuity.current_report()
    assert report["identities"][0]["status"] == "candidate"
    assert report["links"][0]["status"] == "proposed"
    assert tracky_identity_continuity.resolve_entity(HOME_DAVE) is None

    duplicate_pair = copy.deepcopy(proposed)
    duplicate_pair["links"][0]["link_id"] = "abababab-abab-4bab-8bab-abababababab"
    duplicate_pair["links"][0]["revision"] = 2
    try:
        tracky_identity_continuity.ingest_projection(
            duplicate_pair, source="tracky", origin_role="local_governed"
        )
        raise AssertionError("same identity pair acquired a second governing link")
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        assert exc.status_code == 409
        assert "different link" in str(exc)

    confirmed = copy.deepcopy(proposed)
    confirmed["identities"] = [{
        "canonical_identity_id": PERSON_CANON,
        "entity_type": "person",
        "status": "active",
        "members": [HOME_DAVE, OFFICE_DAVE],
        "aliases": ["Dave"],
        "revision": 1,
        "created_at": 1000,
        "updated_at": 1200,
    }]
    confirmed["links"][0]["status"] = "confirmed"
    confirmed["links"][0]["reason"] = "user_confirmed"
    confirmed["links"][0]["evidence"].append({
        "evidence_id": "evidence-2",
        "type": "user_confirmed",
        "source": "user",
        "source_class": "user",
        "confidence": 1.0,
        "at": 1200,
        "consent_scope": {},
        "metadata": {},
    })
    confirmed["links"][0]["confidence"] = .99
    confirmed["links"][0]["revision"] = 2
    confirmed["links"][0]["updated_at"] = 1200
    confirmed["links"][0]["confirmed_at"] = 1200

    second = tracky_identity_continuity.ingest_projection(
        confirmed, source="tracky", origin_role="local_governed"
    )
    assert second["changed"] == 1
    assert tracky_identity_continuity.resolve_entity(HOME_DAVE)["canonical_identity_id"] == PERSON_CANON
    assert tracky_identity_continuity.resolve_entity(OFFICE_DAVE)["canonical_identity_id"] == PERSON_CANON

    retry = tracky_identity_continuity.ingest_projection(
        confirmed, source="tracky", origin_role="local_governed"
    )
    assert retry["idempotent"] == 1 and retry["changed"] == 0

    stale = copy.deepcopy(proposed)
    stale_result = tracky_identity_continuity.ingest_projection(
        stale, source="tracky", origin_role="local_governed"
    )
    assert stale_result["stale"] == 1

    conflict = copy.deepcopy(confirmed)
    conflict["links"][0]["confidence"] = .51
    try:
        tracky_identity_continuity.ingest_projection(
            conflict, source="tracky", origin_role="local_governed"
        )
        raise AssertionError("same-revision identity conflict was accepted")
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        assert exc.status_code == 409
        assert "revision conflicts" in str(exc)

    collision = {
        "protocol": "physical_identity_continuity.v1",
        "identities": [{
            "canonical_identity_id": "88888888-8888-4888-8888-888888888888",
            "entity_type": "person",
            "status": "active",
            "members": [HOME_DAVE, CABIN_DAVE],
            "aliases": [],
            "revision": 1,
            "created_at": 1300,
            "updated_at": 1300,
        }],
        "links": [{
            "link_id": "99999999-9999-4999-8999-999999999999",
            "canonical_identity_id": "88888888-8888-4888-8888-888888888888",
            "entity_type": "person",
            "left_ref": HOME_DAVE,
            "right_ref": CABIN_DAVE,
            "status": "confirmed",
            "reason": "user_confirmed",
            "evidence": [],
            "confidence": 1,
            "revision": 1,
            "created_at": 1300,
            "updated_at": 1300,
            "confirmed_at": 1300,
        }],
        "blocked_pairs": [],
    }
    try:
        tracky_identity_continuity.ingest_projection(
            collision, source="tracky", origin_role="local_governed"
        )
        raise AssertionError("identity collision was accepted")
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        assert exc.status_code == 409
        assert "different active canonical identity" in str(exc)

    cloud = tracky_identity_continuity.cloud_projection(HOME)
    assert cloud["origin_scope"] == "local_governing_site_only"
    assert cloud["cloud_can_confirm_links"] is False
    assert cloud["cloud_can_merge_identities"] is False
    assert cloud["cloud_can_split_identities"] is False
    assert cloud["links"][0]["governing_site_id"] == HOME
    assert cloud["links"][0]["governing_authority_device_id"] == NODE_A
    assert cloud["links"][0]["governing_authority_epoch"] == 1

    # Split/correction is durable and removes canonical resolution without touching local refs.
    split = copy.deepcopy(confirmed)
    split["identities"][0]["status"] = "split"
    split["identities"][0]["members"] = []
    split["identities"][0]["revision"] = 2
    split["identities"][0]["updated_at"] = 1400
    split["links"][0]["status"] = "split"
    split["links"][0]["reason"] = "user_correction"
    split["links"][0]["revision"] = 3
    split["links"][0]["updated_at"] = 1400
    split["links"][0]["split_at"] = 1400
    pair = "|".join(sorted([HOME_DAVE, OFFICE_DAVE]))
    split["blocked_pairs"] = [{"pair": pair, "reason": "user_correction", "at": 1400}]
    split_result = tracky_identity_continuity.ingest_projection(
        split, source="tracky", origin_role="local_governed"
    )
    assert split_result["changed"] == 1
    assert tracky_identity_continuity.resolve_entity(HOME_DAVE) is None
    after_split = tracky_identity_continuity.current_report()
    assert after_split["links"][0]["status"] == "split"
    assert after_split["blocked_pairs"][0]["pair"] == pair
    assert HOME_DAVE in {entity["ref"] for site in tracky_federated_world.current_report()["sites"] for entity in site["entities"]}

    stale_after_split = tracky_identity_continuity.ingest_projection(
        confirmed, source="tracky", origin_role="local_governed"
    )
    assert stale_after_split["stale"] == 1

    relink = copy.deepcopy(confirmed)
    relink["identities"][0]["revision"] = 3
    relink["identities"][0]["updated_at"] = 1500
    relink["links"][0]["revision"] = 4
    relink["links"][0]["updated_at"] = 1500
    try:
        tracky_identity_continuity.ingest_projection(
            relink, source="tracky", origin_role="local_governed"
        )
        raise AssertionError("blocked identity pair was silently re-linked")
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        assert exc.status_code == 409
        assert "blocked" in str(exc)

    # A cloud mirror governed by Home may be accepted at Cabin only when Cabin is a member site.
    tracky_federation_sync.set_local_site_id(CABIN)
    mirror = {
        "protocol": "physical_identity_continuity.v1",
        "identities": [{
            "canonical_identity_id": DEVICE_CANON,
            "entity_type": "device",
            "status": "active",
            "members": [HOME_POCKET, CABIN_POCKET],
            "aliases": ["Pocket"],
            "revision": 1,
            "created_at": 1500,
            "updated_at": 1500,
        }],
        "links": [{
            "link_id": DEVICE_LINK,
            "canonical_identity_id": DEVICE_CANON,
            "entity_type": "device",
            "left_ref": HOME_POCKET,
            "right_ref": CABIN_POCKET,
            "status": "confirmed",
            "reason": "stable_device_credential",
            "evidence": [{
                "evidence_id": "device-evidence",
                "type": "stable_device_credential",
                "source": "device-cert",
                "source_class": "credential",
                "confidence": 1,
                "at": 1500,
                "consent_scope": {},
                "metadata": {},
            }],
            "confidence": 1,
            "auto_confirmed": True,
            "revision": 1,
            "created_at": 1500,
            "updated_at": 1500,
            "confirmed_at": 1500,
            "governing_site_id": HOME,
            "governing_authority_device_id": NODE_A,
            "governing_authority_epoch": 1,
        }],
        "blocked_pairs": [],
    }
    mirror_result = tracky_identity_continuity.ingest_projection(
        mirror, source="vp3_cloud", origin_role="cloud_mirror"
    )
    assert mirror_result["changed"] == 1
    assert tracky_identity_continuity.resolve_entity(CABIN_POCKET)["canonical_identity_id"] == DEVICE_CANON
    assert not [item for item in tracky_identity_continuity.cloud_projection(CABIN)["links"] if item["link_id"] == DEVICE_LINK]

    unrelated = copy.deepcopy(mirror)
    unrelated["links"][0]["link_id"] = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    unrelated["links"][0]["left_ref"] = HOME_DAVE
    unrelated["links"][0]["right_ref"] = OFFICE_DAVE
    unrelated["links"][0]["left_site_id"] = HOME
    unrelated["links"][0]["right_site_id"] = OFFICE
    unrelated["links"][0]["revision"] = 1
    unrelated["identities"][0] = {
        "canonical_identity_id": DEVICE_CANON,
        "entity_type": "device",
        "status": "active",
        "members": [HOME_DAVE, OFFICE_DAVE],
        "aliases": [],
        "revision": 2,
        "created_at": 1600,
        "updated_at": 1600,
    }
    try:
        tracky_identity_continuity.ingest_projection(
            unrelated, source="vp3_cloud", origin_role="cloud_mirror"
        )
        raise AssertionError("unrelated identity mirror was accepted")
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        assert exc.status_code == 409

    raw = copy.deepcopy(mirror)
    raw["links"][0]["revision"] = 2
    raw["links"][0]["evidence"][0]["embedding"] = [1, 2, 3]
    try:
        tracky_identity_continuity.normalize_projection(raw)
        raise AssertionError("raw identity evidence was accepted")
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        assert exc.status_code == 422

    package = tracky_physical_context._cloud_payload()
    assert package["payload"]["capabilities"]["identity_continuity_protocol"] == "physical_identity_continuity.v1"
    assert "identity_continuity" in package["payload"]

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    migration = (ROOT / "database" / "migrations" / "048_tracky_identity_continuity.sql").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/identity-continuity"' in api
    assert '@router.post("/api/v1/tracky/identity-continuity' not in api
    assert "tracky_identity_blocked_pairs" in migration

print("Tracky V2.78 Section 5 OTRO identity continuity integration: PASS")

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
POCKET = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

with tempfile.TemporaryDirectory(prefix="tracky-v278-s4-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        tracky_federation_sync,
        tracky_mobile_transition,
        tracky_physical_context,
        tracky_site_topology,
    )

    initialize_database()

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert versions == list(range(1, 58))
        for table in ("tracky_mobile_transitions", "tracky_mobile_transition_history"):
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

    def transition(revision: int, state: str, *, destination: str = OFFICE, temporary=None) -> dict:
        return {
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
                "transition_id": "trip-1",
                "subject_kind": "mobile_device",
                "subject_id": POCKET,
                "subject_scope": "stable_mobile_device",
                "source_site_id": HOME,
                "destination_site_id": destination,
                "state": state,
                "previous_state": "departing" if state != "departing" else "",
                "resume_state": "",
                "state_reason": "test",
                "confidence": 0.88,
                "destination_confidence": 0.76,
                "temporary_context": temporary,
                "evidence": [{"type": "mobile_motion", "confidence": 0.9}],
                "revision": revision,
                "identity_linking": False,
                "authority_scope": "source_site",
                "started_at": 1000,
                "state_changed_at": 1000 + revision,
                "updated_at": 1100 + revision,
                "arrived_at": 1200 + revision if state == "arrived" else None,
                "canceled_at": None,
                "offline_since": None,
            }],
        }

    first = tracky_mobile_transition.ingest_projection(
        transition(1, "departing"), source="tracky", origin_role="local_authority"
    )
    assert first == {"accepted": True, "changed": 1, "stale": 0, "idempotent": 0}

    second = tracky_mobile_transition.ingest_projection(
        transition(2, "in_transit"), source="tracky", origin_role="local_authority"
    )
    assert second["changed"] == 1

    retry = tracky_mobile_transition.ingest_projection(
        transition(2, "in_transit"), source="tracky", origin_role="local_authority"
    )
    assert retry["idempotent"] == 1 and retry["changed"] == 0

    stale = tracky_mobile_transition.ingest_projection(
        transition(1, "departing"), source="tracky", origin_role="local_authority"
    )
    assert stale["stale"] == 1

    conflict = transition(2, "in_transit")
    conflict["transitions"][0]["confidence"] = 0.31
    try:
        tracky_mobile_transition.ingest_projection(conflict, source="tracky", origin_role="local_authority")
        raise AssertionError("same-revision conflicting transition was accepted")
    except tracky_mobile_transition.TrackyMobileTransitionError as exc:
        assert exc.status_code == 409
        assert "revision conflicts" in str(exc)

    temp = transition(3, "temporary_context", temporary={
        "id": "hotel-room-410",
        "label": "Hotel room",
        "observed_at": 1300,
        "confidence": 0.82,
        "durable_site": False,
        "site_authority": False,
    })
    temp_result = tracky_mobile_transition.ingest_projection(
        temp, source="tracky", origin_role="local_authority"
    )
    assert temp_result["changed"] == 1
    current = tracky_mobile_transition.current_report()
    assert current["transitions"][0]["temporary_context"]["durable_site"] is False
    assert current["transitions"][0]["temporary_context"]["site_authority"] is False

    # Local source-only origin is enforced.
    wrong_source = copy.deepcopy(temp)
    wrong_source["transitions"][0]["transition_id"] = "trip-office-origin"
    wrong_source["transitions"][0]["source_site_id"] = OFFICE
    wrong_source["transitions"][0]["destination_site_id"] = HOME
    try:
        tracky_mobile_transition.ingest_projection(
            wrong_source, source="tracky", origin_role="local_authority"
        )
        raise AssertionError("remote source transition was re-originated locally")
    except tracky_mobile_transition.TrackyMobileTransitionError as exc:
        assert exc.status_code == 409
        assert "local site" in str(exc)

    # Finish the local Pocket transition before a new source site can own another active transition.
    arrived = transition(4, "arrived")
    arrived_result = tracky_mobile_transition.ingest_projection(
        arrived, source="tracky", origin_role="local_authority"
    )
    assert arrived_result["changed"] == 1

    # Cloud mirrors may be accepted only when explicitly routed to this local destination.
    mirror = copy.deepcopy(wrong_source)
    mirror["transitions"][0]["state"] = "arriving"
    mirror["transitions"][0]["revision"] = 2
    mirror_result = tracky_mobile_transition.ingest_projection(
        mirror, source="vp3_cloud", origin_role="cloud_mirror"
    )
    assert mirror_result["changed"] == 1

    wrong_destination = copy.deepcopy(mirror)
    wrong_destination["transitions"][0]["transition_id"] = "trip-bad-destination"
    wrong_destination["transitions"][0]["destination_site_id"] = CABIN
    try:
        tracky_mobile_transition.ingest_projection(
            wrong_destination, source="vp3_cloud", origin_role="cloud_mirror"
        )
        raise AssertionError("Cloud mirror for another destination was accepted")
    except tracky_mobile_transition.TrackyMobileTransitionError as exc:
        assert exc.status_code == 409
        assert "different destination site" in str(exc)

    # Person/object cross-site identity linking remains explicitly out of scope.
    linked = transition(4, "arriving")
    linked["identity_linking"] = True
    try:
        tracky_mobile_transition.normalize_projection(linked)
        raise AssertionError("cross-site identity linking was accepted")
    except tracky_mobile_transition.TrackyMobileTransitionError as exc:
        assert "Section 5" in str(exc)

    raw = transition(4, "arriving")
    raw["transitions"][0]["evidence"][0]["frame_data"] = "private"
    try:
        tracky_mobile_transition.normalize_projection(raw)
        raise AssertionError("raw perception entered mobile transition projection")
    except tracky_mobile_transition.TrackyMobileTransitionError as exc:
        assert exc.status_code == 422

    # Cloud upload carries only locally authoritative source transitions and current authority epoch.
    package = tracky_physical_context._cloud_payload()
    mobile = package["payload"]["mobile_transitions"]
    assert mobile["origin_scope"] == "local_source_site_only"
    assert package["payload"]["capabilities"]["mobile_transitions"] is True
    assert package["payload"]["capabilities"]["mobile_transition_protocol"] == "physical_mobile_transition.v1"
    assert all(item["source_site_id"] == HOME for item in mobile["transitions"])
    assert all(item["origin_role"] == "local_authority" for item in mobile["transitions"])
    assert all(item["source_authority_device_id"] == NODE_A for item in mobile["transitions"])
    assert all(item["source_authority_epoch"] == 1 for item in mobile["transitions"])

    context = tracky_mobile_transition.agent_context()
    assert context["protocol"] == "physical_mobile_transition.v1"
    assert context["active_count"] >= 1
    assert all(item["identity_linking"] is False for item in context["transitions"])

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "tracky_mobile_transition.py").read_text(encoding="utf-8")
    migration = (ROOT / "database" / "migrations" / "047_tracky_mobile_transitions.sql").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/mobile-transitions"' in api
    assert '@router.post("/api/v1/tracky/mobile-transitions' not in api
    assert "source_site" in service and "cloud_mirror" in service
    assert "uq_tracky_mobile_transition_active_subject" in migration

print("Tracky V2.78 Section 4 OTRO mobile transition integration: PASS")

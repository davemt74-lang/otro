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
NODE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BACKUP = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
POCKET = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
DESK = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

with tempfile.TemporaryDirectory(prefix="tracky-v278-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import tracky_physical_context, tracky_site_topology  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [int(row["version"]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions == list(range(1, 52))
        for table in ("tracky_site_topology_state", "tracky_sites", "tracky_site_devices", "tracky_site_authority", "tracky_site_relationships"):
            assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    tracky_site_topology.register_site(site_id=HOME, label="Home", aliases=["home-main"])
    tracky_site_topology.register_site(site_id=OFFICE, label="Office", kind="shared_site")

    node = tracky_site_topology.register_device(
        device_id=NODE, label="Node", site_id=HOME, hardware_profile="Node", trust_state="trusted",
        roles=["site_authority", "perception", "persistence"],
        capabilities={"site_authority_eligible": True, "reconciliation": True, "physical_context": True},
    )
    assert node["hardware_profile"] == "node"
    assert node["mobility"] == "fixed"

    authority = tracky_site_topology.claim_site_authority(site_id=HOME, device_id=NODE)
    assert authority["authority_epoch"] == 1
    retry = tracky_site_topology.claim_site_authority(site_id=HOME, device_id=NODE)
    assert retry["authority_epoch"] == 1
    assert retry["idempotent"] is True

    tracky_site_topology.register_device(
        device_id=BACKUP, label="Backup Node", site_id=HOME, hardware_profile="Node", trust_state="trusted",
        roles=["site_authority", "persistence"], capabilities={"site_authority_eligible": True},
    )
    try:
        tracky_site_topology.claim_site_authority(site_id=HOME, device_id=BACKUP)
        raise AssertionError("second authority claimed without explicit replacement")
    except tracky_site_topology.TrackySiteTopologyError as exc:
        assert exc.status_code == 409

    replaced = tracky_site_topology.claim_site_authority(site_id=HOME, device_id=BACKUP, replace=True, reason="planned_handoff")
    assert replaced["authority_epoch"] == 2

    # An active authority cannot silently lose its site/trust/role/capability via upsert.
    for kwargs in (
        {"site_id": OFFICE, "trust_state": "trusted", "roles": ["site_authority"], "capabilities": {"site_authority_eligible": True}},
        {"site_id": HOME, "trust_state": "revoked", "roles": ["site_authority"], "capabilities": {"site_authority_eligible": True}},
        {"site_id": HOME, "trust_state": "trusted", "roles": ["perception"], "capabilities": {"site_authority_eligible": True}},
        {"site_id": HOME, "trust_state": "trusted", "roles": ["site_authority"], "capabilities": {"site_authority_eligible": False}},
    ):
        try:
            tracky_site_topology.register_device(device_id=BACKUP, label="Backup Node", hardware_profile="Node", **kwargs)
            raise AssertionError("authority invariant was bypassed")
        except tracky_site_topology.TrackySiteTopologyError as exc:
            assert "Release site authority" in str(exc)

    try:
        tracky_site_topology.register_device(
            device_id=POCKET, label="Pocket", site_id=HOME, hardware_profile="Pocket", trust_state="trusted",
            roles=["site_authority"], capabilities={"site_authority_eligible": True},
        )
        raise AssertionError("mobile site authority accepted")
    except tracky_site_topology.TrackySiteTopologyError as exc:
        assert "Mobile devices" in str(exc)

    tracky_site_topology.register_device(
        device_id=POCKET, label="Pocket", site_id=HOME, hardware_profile="Pocket", trust_state="trusted",
        roles=["identity_continuity", "mobile_presence", "transition_sensor"],
        capabilities={"location_transition": True, "camera": True},
    )
    tracky_site_topology.register_device(
        device_id=DESK, label="Desk", site_id=HOME, hardware_profile="Desk", trust_state="trusted",
        roles=["interaction", "perception"], capabilities={"camera": True, "display": True},
        metadata={"placement": "office"},
    )
    tracky_site_topology.upsert_relationship(subject_id=DESK, relation_type="observes", object_id=HOME)
    tracky_site_topology.upsert_relationship(subject_id=POCKET, relation_type="travels_with", object_id=DESK)

    try:
        tracky_site_topology.register_device(
            device_id=DESK, label="Desk", site_id=HOME, hardware_profile="Desk",
            metadata={"frame_data": "private"},
        )
        raise AssertionError("raw perception topology metadata accepted")
    except tracky_site_topology.TrackySiteTopologyError as exc:
        assert exc.status_code == 422

    topology = tracky_site_topology.current_topology()
    assert topology["protocol"] == "physical_site_topology.v1"
    assert topology["revision"] >= 8
    assert len(topology["sites"]) == 2
    assert len(topology["devices"]) == 4
    assert topology["sites"][0]["authority_device_id"] == BACKUP

    cloud = tracky_site_topology.cloud_summary()
    assert cloud["summary_only"] is True
    assert cloud["cloud_read_only"] is True
    assert cloud["authority_assignment"] == "local_only"
    assert "metadata" not in str(cloud)
    assert cloud["devices"][2]["hardware_profile"] in {"desk", "pocket", "node"}

    cap = tracky_site_topology.public_capability()
    assert cap["version"] == "2.78"
    assert cap["mobile_authority"] is False
    assert cap["authority_assignment"] == "local_only"

    physical_cap = tracky_physical_context.public_capability()
    assert physical_cap["version"] == "2.78"
    assert physical_cap["site_topology"]["protocol"] == "physical_site_topology.v1"

    context = tracky_physical_context.current_context()
    assert context["site_topology"]["protocol"] == "physical_site_topology.v1"

    package = tracky_physical_context._cloud_payload()
    wire = package["payload"]["site_topology"]
    assert wire["protocol"] == "physical_site_topology.v1"
    assert wire["summary_only"] is True
    assert wire["cloud_read_only"] is True
    assert wire["authority_assignment"] == "local_only"
    assert package["payload"]["capabilities"]["site_topology"] is True
    assert package["payload"]["capabilities"]["site_topology_protocol"] == "physical_site_topology.v1"

    api = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "tracky_site_topology.py").read_text(encoding="utf-8")
    migration = (ROOT / "database" / "migrations" / "044_tracky_site_topology.sql").read_text(encoding="utf-8")
    assert '"/api/v1/tracky/site-topology"' in api
    assert '@router.post("/api/v1/tracky/site-topology' not in api
    assert "uq_tracky_site_active_authority" in migration
    assert "cloud_read_only" in service
    assert "authority_assignment" in service

print("Tracky V2.78 OTRO site topology integration: PASS")

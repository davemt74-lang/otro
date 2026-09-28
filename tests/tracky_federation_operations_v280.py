from app.services import tracky_federation_operations as operations

HOME = "11111111-1111-4111-8111-111111111111"
OFFICE = "22222222-2222-4222-8222-222222222222"
NODE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DESK = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def fixture(peer_status="current"):
    return {
        "protocol": "physical_site_topology.v1",
        "revision": 17,
        "sites": [
            {"id": HOME, "label": "Home", "status": "active", "authority_device_id": NODE, "authority_epoch": 3},
            {"id": OFFICE, "label": "Office", "status": "active", "authority_device_id": DESK, "authority_epoch": 2},
        ],
        "devices": [
            {"id": NODE, "site_id": HOME, "label": "Home Node", "hardware_profile": "node", "mobility": "fixed", "trust_state": "trusted", "roles": ["site_authority"], "capabilities": {"site_authority_eligible": True}},
            {"id": DESK, "site_id": OFFICE, "label": "Office Desk", "hardware_profile": "desk", "mobility": "fixed", "trust_state": "trusted", "roles": ["site_authority"], "capabilities": {"site_authority_eligible": True}},
        ],
        "relationships": [{"subject_id": HOME, "type": "peers_with", "object_id": OFFICE}],
    }, {
        "protocol": "physical_federation_reconciliation.v1",
        "local_site_id": HOME,
        "peers": [{"remote_site_id": OFFICE, "status": peer_status}],
    }


def run():
    topology, reconciliation = fixture()
    report = operations.build_snapshot(topology, reconciliation, local_site_id=HOME)
    assert report["protocol"] == operations.FEDERATION_OPERATIONS_PROTOCOL
    assert report["health"] == "healthy"
    assert report["summary"]["profile_counts"] == {"desk": 1, "node": 1}
    assert report["summary"]["authority_count"] == 2
    assert report["read_only"] is True
    assert report["authority_assignment"] == "origin_only"

    topology, reconciliation = fixture("partitioned")
    report = operations.build_snapshot(topology, reconciliation, local_site_id=HOME)
    office = next(row for row in report["sites"] if row["id"] == OFFICE)
    assert report["health"] == "critical"
    assert office["health"] == "critical"
    assert office["authority"]["device_id"] == DESK
    assert any(row["code"] == "federation_partitioned" for row in report["issues"])

    for state in ("unknown", "suspect", "reconciling", "stale"):
        topology, reconciliation = fixture(state)
        report = operations.build_snapshot(topology, reconciliation, local_site_id=HOME)
        office = next(row for row in report["sites"] if row["id"] == OFFICE)
        assert office["health"] == "degraded"

    topology, reconciliation = fixture()
    topology["sites"][1]["authority_device_id"] = ""
    report = operations.build_snapshot(topology, reconciliation, local_site_id=HOME)
    assert next(row for row in report["sites"] if row["id"] == OFFICE)["health"] == "critical"

    topology, reconciliation = fixture()
    topology["devices"][1]["trust_state"] = "revoked"
    report = operations.build_snapshot(topology, reconciliation, local_site_id=HOME)
    office = next(row for row in report["sites"] if row["id"] == OFFICE)
    assert office["authority"]["status"] == "invalid"
    assert any(row["code"] == "authority_device_not_trusted" for row in report["issues"])

    capability = operations.public_capability()
    assert capability["version"] == "2.80"
    assert capability["read_only"] is True
    assert capability["authority_mutation"] is False
    assert capability["identity_mutation"] is False
    assert capability["hardware_profiles"] == ["node", "desk", "studio", "team_node", "pocket", "custom"]
    print("TRACKY_V280_FEDERATION_OPERATIONS=PASS")


if __name__ == "__main__":
    run()

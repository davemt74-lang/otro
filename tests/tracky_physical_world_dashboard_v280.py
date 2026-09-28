from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import tracky_physical_world_dashboard as dashboard

HOME = "11111111-1111-4111-8111-111111111111"
OFFICE = "22222222-2222-4222-8222-222222222222"
NODE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DESK = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def fixture(selected=OFFICE):
    operations = {
        "local_site_id": HOME,
        "sites": [
            {"id": HOME, "label": "Home", "health": "healthy", "federation": {"status": "current"}, "authority": {"device_id": NODE, "epoch": 3}},
            {"id": OFFICE, "label": "Office", "health": "healthy", "federation": {"status": "current"}, "authority": {"device_id": DESK, "epoch": 2}},
        ],
        "devices": [
            {"id": NODE, "site_id": HOME, "label": "Home Node", "hardware_profile": "node", "trust_state": "trusted"},
            {"id": DESK, "site_id": OFFICE, "label": "Office Desk", "hardware_profile": "desk", "trust_state": "trusted"},
        ],
    }
    world = {
        "sites": [
            {"site_id": HOME, "revision": 4, "entities": [{"local_id": "home-room", "type": "room", "label": "Living Room", "state": "observed", "confidence": .95, "observed_at": 100}], "relations": []},
            {"site_id": OFFICE, "revision": 7, "observed_at": 200, "entities": [
                {"local_id": "office-room", "type": "room", "label": "Studio", "state": "user-confirmed", "confidence": 1, "observed_at": 190},
                {"local_id": "person-dave", "type": "person", "label": "Dave", "state": "observed", "confidence": .9, "observed_at": 200},
                {"local_id": "mug", "type": "object", "label": "Coffee Mug", "state": "last-known", "confidence": .8, "observed_at": 180},
                {"local_id": "camera", "type": "camera", "label": "Desk Camera", "state": "observed", "confidence": .99, "observed_at": 200},
            ], "relations": [
                {"subject_local_id": "person-dave", "predicate": "located_in", "object_local_id": "office-room", "confidence": .95, "temporal_state": "current", "as_of": 200},
                {"subject_local_id": "mug", "predicate": "located_on", "object_local_id": "office-room", "confidence": .7, "temporal_state": "last_seen", "as_of": 180},
            ]},
        ]
    }
    context = {"agent_state": "current", "physical_state": "present", "current_site": {"site_id": HOME}}
    return operations, world, context, selected


def run():
    operations, world, context, selected = fixture()
    report = dashboard.build_dashboard(operations, world, context, selected_site_id=selected)
    assert report["protocol"] == dashboard.PHYSICAL_WORLD_DASHBOARD_PROTOCOL
    assert report["selected_site"]["site_id"] == OFFICE
    assert report["selected_site"]["basis"] == "explicit_user_selection"
    assert report["agent_context"]["view_site_id"] == OFFICE
    assert report["agent_context"]["physical_current_site_id"] == HOME
    assert report["agent_context"]["changes_physical_authority"] is False
    assert report["agent_context"]["changes_physical_location"] is False
    assert report["counts"] == {"rooms": 1, "people": 1, "objects": 1, "world_devices": 1, "hardware_units": 1}
    assert report["people"][0]["location"]["location_label"] == "Studio"
    assert report["people"][0]["identity_scope"] == "site_local"
    assert report["hardware_units"][0]["is_authority"] is True

    operations, world, context, _ = fixture()
    unavailable = dashboard.build_dashboard(
        operations, world, context,
        selected_site_id="33333333-3333-4333-8333-333333333333",
    )
    assert unavailable["selected_site"]["site_id"] == HOME
    assert unavailable["selected_site"]["basis"] == "current_agent_site"
    assert unavailable["issues"][0]["code"] == "requested_site_not_available"

    cap = dashboard.public_capability()
    assert cap["site_switching"] is True
    assert cap["agent_context_follows_selected_site"] is True
    assert cap["authority_mutation"] is False
    assert cap["physical_location_mutation"] is False
    assert cap["cross_site_identity_merge"] is False
    print("TRACKY_V280_PHYSICAL_WORLD_DASHBOARD=PASS")


if __name__ == "__main__":
    run()

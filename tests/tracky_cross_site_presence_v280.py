from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import tracky_cross_site_presence as presence

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"
POCKET="cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NODE="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def transition(state="in_transit"):
    return {
        "transition_id":"trip-pocket-1","subject_kind":"mobile_device","subject_id":POCKET,
        "source_site_id":HOME,"destination_site_id":OFFICE,"state":state,
        "previous_state":"departing","state_reason":"test","confidence":.88,
        "destination_confidence":.8,"revision":3,"started_at":1000,
        "state_changed_at":1200,"updated_at":1250,
        "arrived_at":1300 if state=="arrived" else None,
        "offline_since":1220 if state=="offline" else None,
        "temporary_context":{"id":"hotel-room","label":"Hotel","confidence":.7,"durable_site":False,"site_authority":False} if state=="temporary_context" else None,
        "evidence":[
            {"type":"source_absence_confirmed","site_id":HOME,"confidence":.95,"observed_at":1100},
            {"type":"destination_candidate","site_id":OFFICE,"confidence":.8,"observed_at":1200},
        ],
        "identity_linking":False,
    }


def fixture(state="in_transit"):
    operations={
        "sites":[
            {"id":HOME,"label":"Home","health":"healthy","federation":{"status":"current"},"authority":{"device_id":NODE,"epoch":2}},
            {"id":OFFICE,"label":"Office","health":"healthy","federation":{"status":"current"},"authority":{"device_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","epoch":1}},
        ],
        "devices":[{"id":POCKET,"site_id":HOME,"label":"Dave Pocket","hardware_profile":"pocket"}],
    }
    mobile={"transitions":[transition(state)]}
    return operations,mobile


def run():
    operations,mobile=fixture("in_transit")
    report=presence.build_report(operations,mobile)
    assert report["protocol"]==presence.CROSS_SITE_PRESENCE_PROTOCOL
    assert report["active_count"]==1
    item=report["active_transitions"][0]
    assert item["last_confirmed_site"]["site_id"]==HOME
    assert item["current_presence"]["status"]=="in_transit"
    assert item["current_presence"]["confirmed"] is False
    assert item["may_claim_present_at_destination"] is False
    assert report["agent_context"]["destination_claim_rule"]=="present_at_destination_only_after_arrived"

    operations,mobile=fixture("arrived")
    arrived=presence.build_report(operations,mobile)["transitions"][0]
    assert arrived["current_presence"]["site_id"]==OFFICE
    assert arrived["current_presence"]["confirmed"] is True
    assert arrived["last_confirmed_site"]["site_id"]==OFFICE
    assert arrived["may_claim_present_at_destination"] is True
    assert arrived["authority"]["authority_transfer"] is False
    assert arrived["identity"]["cross_site_merge"] is False

    operations,mobile=fixture("temporary_context")
    temp=presence.build_report(operations,mobile)["active_transitions"][0]["current_presence"]
    assert temp["durable_site"] is False and temp["site_authority"] is False

    operations,mobile=fixture("offline")
    offline=presence.build_report(operations,mobile)["active_transitions"][0]["current_presence"]
    assert offline["status"]=="offline" and offline["confirmed"] is False

    operations,mobile=fixture("arriving")
    raw=transition("arriving")
    history=[
        {"transition_id":"trip-pocket-1","revision":1,"fingerprint":"a","snapshot":{**raw,"state":"departing","revision":1,"state_changed_at":1000}},
        {"transition_id":"trip-pocket-1","revision":2,"fingerprint":"b","snapshot":{**raw,"state":"in_transit","revision":2,"state_changed_at":1100}},
        {"transition_id":"trip-pocket-1","revision":3,"fingerprint":"c","snapshot":{**raw,"state":"arriving","revision":3,"state_changed_at":1200}},
    ]
    report=presence.build_report(operations,mobile,history=history)
    assert report["history_source"]=="immutable_transition_revision_history"
    assert [row["state"] for row in report["timeline"]]==["arriving","in_transit","departing"]
    assert all(row["immutable"] for row in report["timeline"])

    ui_index=(ROOT/"ui/index.html").read_text(encoding="utf-8")
    ui_presence=(ROOT/"ui/cross-site-presence-v280.js").read_text(encoding="utf-8")
    ui_dashboard=(ROOT/"ui/physical-world-dashboard-v280.js").read_text(encoding="utf-8")
    main_source=(ROOT/"app/main.py").read_text(encoding="utf-8")
    paired_source=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
    spec=(ROOT/"HomeServer.spec").read_text(encoding="utf-8")
    assert "cross-site-presence-v280.css" in ui_index
    assert "cross-site-presence-v280.js" in ui_index
    assert 'id="crossSiteActive"' in ui_index
    assert 'id="crossSiteTimeline"' in ui_index
    assert "/api/v1/control/cross-site-presence" in main_source
    assert "/api/v1/tracky/cross-site-presence" in paired_source
    assert "loadCrossSitePresence" in ui_dashboard
    assert "('ui', 'ui')" in spec
    for mutation in ("method:'POST'", 'method:"POST"', "method:'PUT'", "method:'PATCH'", "method:'DELETE'"):
        assert mutation not in ui_presence, f"Section 4 cross-site presence must remain read-only: {mutation}"

    cap=presence.public_capability()
    assert cap["transition_history"] is True
    assert cap["destination_claim_requires_arrived"] is True
    assert cap["authority_mutation"] is False
    assert cap["physical_location_mutation"] is False
    assert cap["cross_site_identity_merge"] is False
    print("TRACKY_V280_CROSS_SITE_PRESENCE=PASS")


if __name__=="__main__":
    run()

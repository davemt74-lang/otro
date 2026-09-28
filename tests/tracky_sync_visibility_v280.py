from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.services import tracky_sync_visibility as visibility

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"
CABIN="33333333-3333-4333-8333-333333333333"
NOW=1759061100000


def fixture(status="reconciling"):
    operations={"local_site_id":HOME,"sites":[
        {"id":HOME,"label":"Home","world_revision":12,"authority":{"epoch":4},"federation":{"status":"current"}},
        {"id":OFFICE,"label":"Office","world_revision":18,"authority":{"epoch":3},"federation":{"status":status}},
        {"id":CABIN,"label":"Cabin","world_revision":7,"authority":{"epoch":1},"federation":{"status":"partitioned"}},
    ]}
    reconciliation={"local_site_id":HOME,"peers":[
        {"remote_site_id":OFFICE,"status":status,"stale_since":"2025-09-28T12:00:00Z","reconciling_since":"2025-09-28T12:02:00Z","last_contact_at":"2025-09-28T12:04:00Z",
         "retry_count":2,"next_retry_at":"2025-09-28T12:06:00Z","local_revision":15,"local_fingerprint":"abc","local_authority_epoch":2,
         "remote_revision":18,"remote_fingerprint":"def","remote_authority_epoch":3,"last_error":"reconciliation_incomplete"},
        {"remote_site_id":CABIN,"status":"partitioned","stale_since":"2025-09-28T11:30:00Z","partitioned_at":"2025-09-28T11:31:00Z",
         "local_revision":7,"local_fingerprint":"cab","local_authority_epoch":1,"remote_revision":7,"remote_fingerprint":"cab","remote_authority_epoch":1,"last_error":"transport_partition"},
    ],"runs":[
        {"reconciliation_id":"r1","remote_site_id":OFFICE,"status":"running","request_mode":"full_snapshot","reason":"revision_gap",
         "local_revision":15,"remote_revision":18,"authority_epoch":3,"created_at":"2025-09-28T12:02:00Z",
         "details_json":'{"authority_epoch_changed":true}'},
    ]}
    sync={"local_site_id":HOME}
    transitions=[{"transition_id":"trip-1","subject_label":"Pocket","state":"arriving","active":True,
                  "source_site":{"site_id":HOME},"destination_site":{"site_id":OFFICE}}]
    return operations,reconciliation,sync,transitions


def run():
    operations,reconciliation,sync,transitions=fixture()
    report=visibility.build_report(operations,reconciliation,sync,cross_site_transitions=transitions,now_ms=NOW)
    assert report["protocol"]==visibility.FEDERATION_SYNC_VISIBILITY_PROTOCOL
    office=next(row for row in report["sites"] if row["site_id"]==OFFICE)
    assert office["status"]=="reconciling"
    assert office["revision_gap"]==3
    assert office["authority_epoch_mismatch"] is True
    assert office["retry_count"]==2
    assert office["catch_up"]["applied_revision"]==15
    assert office["catch_up"]["target_revision"]==18
    assert 0.8<office["catch_up"]["progress"]<1
    assert office["remote_authority_promotion"] is False

    cabin=next(row for row in report["sites"] if row["site_id"]==CABIN)
    assert cabin["status"]=="partitioned"
    assert cabin["fresh"] is False
    assert cabin["reconciliation_required"] is True
    assert report["reconciliation_runs"][0]["immutable"] is True

    hint=report["transition_sync_hints"][0]
    assert hint["destination_sync_status"]=="reconciling"
    assert hint["destination_presence_claim_blocked"] is True
    assert "do not infer arrival" in hint["note"]

    dashboard=visibility.annotate_dashboard({
        "selected_site":{"site_id":OFFICE},"people":[{"label":"Dave"}],
        "rooms":[],"objects":[],"world_devices":[],"agent_context":{}
    },report)
    assert dashboard["federation_freshness"]["status"]=="reconciling"
    assert dashboard["people"][0]["federation_freshness"]["fresh"] is False
    assert dashboard["agent_context"]["sync_state"]=="reconciling"

    operations,reconciliation,sync,transitions=fixture("failed")
    peer=reconciliation["peers"][0]
    peer.update({"local_revision":18,"remote_revision":18,"local_fingerprint":"left","remote_fingerprint":"right","local_authority_epoch":3,"remote_authority_epoch":3,"last_error":"same_revision_fingerprint_conflict"})
    office=next(row for row in visibility.build_report(operations,reconciliation,sync,now_ms=NOW)["sites"] if row["site_id"]==OFFICE)
    assert office["fingerprint_conflict"] is True
    assert office["conflict_code"]=="same_revision_fingerprint_conflict"

    ui_index=(ROOT/"ui/index.html").read_text(encoding="utf-8")
    ui_script=(ROOT/"ui/federation-sync-visibility-v280.js").read_text(encoding="utf-8")
    world_script=(ROOT/"ui/physical-world-dashboard-v280.js").read_text(encoding="utf-8")
    main_source=(ROOT/"app/main.py").read_text(encoding="utf-8")
    paired_source=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
    cloud_source=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
    spec=(ROOT/"HomeServer.spec").read_text(encoding="utf-8")
    assert "federation-sync-visibility-v280.css" in ui_index
    assert "federation-sync-visibility-v280.js" in ui_index
    assert 'id="syncVisibilitySites"' in ui_index
    assert 'id="physicalWorldSyncWarning"' in ui_index
    assert "/api/v1/control/federation-sync-visibility" in main_source
    assert "/api/v1/tracky/federation-sync-visibility" in paired_source
    assert "federation_sync_visibility" in cloud_source
    assert "federation_sync_visibility_protocol" in cloud_source
    assert "loadFederationSyncVisibility" in ui_script
    assert "Federation freshness:" in world_script
    assert "('ui', 'ui')" in spec
    for mutation in ("method:'POST'", 'method:"POST"', "method:'PUT'", "method:'PATCH'", "method:'DELETE'"):
        assert mutation not in ui_script, f"Section 5 visibility must remain read-only: {mutation}"

    cap=visibility.public_capability()
    assert cap["retry_visibility"] is True
    assert cap["physical_world_freshness_annotations"] is True
    assert cap["read_only"] is True
    assert cap["authority_mutation"] is False
    assert cap["cloud_can_mark_destination_current"] is False
    print("TRACKY_V280_SYNC_VISIBILITY=PASS")


if __name__=="__main__":
    run()

from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.services import tracky_federation_agent_health as health

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"
HOME_DEVICE="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OFFICE_DEVICE="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def fixture(status="partitioned", fresh=False):
    operations={
        "local_site_id":HOME,
        "sites":[
            {"id":HOME,"label":"Home","status":"active","authority":{"status":"current","device_id":HOME_DEVICE,"epoch":4},"federation":{"status":"current"}},
            {"id":OFFICE,"label":"Office","status":"active","authority":{"status":"current","device_id":OFFICE_DEVICE,"epoch":2},"federation":{"status":status}},
        ],
        "devices":[
            {"device_id":HOME_DEVICE,"site_id":HOME,"runtime_status":"online"},
            {"device_id":OFFICE_DEVICE,"site_id":OFFICE,"runtime_status":"online"},
        ],
    }
    sync={
        "local_site_id":HOME,
        "sites":[
            {"site_id":HOME,"status":"current","fresh":True,"reconciliation_required":False},
            {"site_id":OFFICE,"status":status,"fresh":fresh,"reconciliation_required":status!="current","revision_gap":2 if status=="reconciling" else 0,"stale_age_ms":120000},
        ],
    }
    access={"peers":[{"site_id":OFFICE,"policy_peer_allowed":True}]}
    relay={"cloud":{"state":"connected","connected":True,"paired":True,"transport":"vp3_https"}}
    return operations,sync,access,relay


def run():
    operations,sync,access,relay=fixture()
    report=health.build_report(operations,sync,access,relay,previous={},now_ms=1000)
    office=next(row for row in report["sites"] if row["site_id"]==OFFICE)
    assert report["protocol"]==health.FEDERATION_AGENT_HEALTH_PROTOCOL
    assert report["local_site_id"]==HOME
    assert office["state"]=="partitioned"
    assert office["severity"]=="critical"
    assert office["recovery_complete"] is False
    assert office["trust"]["physical_claims"]=="do_not_claim_current"
    assert any(event["event_type"]=="site.partitioned" for event in report["events"])

    operations,sync,access,relay=fixture("reconciling",False)
    recovering=health.build_report(operations,sync,access,relay,previous=report,now_ms=2000)
    office=next(row for row in recovering["sites"] if row["site_id"]==OFFICE)
    assert office["state"]=="recovering"
    assert office["recovery_complete"] is False
    assert "not complete" in office["message"].lower()
    assert any(event["event_type"]=="site.recovering" for event in recovering["events"])

    operations,sync,access,relay=fixture("current",True)
    recovered=health.build_report(operations,sync,access,relay,previous=recovering,now_ms=3000)
    office=next(row for row in recovered["sites"] if row["site_id"]==OFFICE)
    assert office["state"]=="current"
    assert office["recovery_complete"] is True
    recovery_event=next(event for event in recovered["events"] if event.get("site_id")==OFFICE)
    assert recovery_event["event_type"]=="site.recovered"
    assert recovery_event["voice_eligible"] is True

    operations,sync,access,relay=fixture("current",True)
    next(row for row in operations["devices"] if row["site_id"]==OFFICE)["runtime_status"]="offline"
    failed=health.build_report(operations,sync,access,relay,previous={},now_ms=1000)
    office=next(row for row in failed["sites"] if row["site_id"]==OFFICE)
    assert office["state"]=="failed"
    assert office["cause"]=="authority_device_offline"

    operations,sync,access,relay=fixture("current",True)
    relay["cloud"]={"state":"offline","connected":False,"paired":True,"transport":"vp3_https","last_error":"relay unavailable"}
    relay_failed=health.build_report(operations,sync,access,relay,previous={},now_ms=1000)
    assert relay_failed["relay_health"]["state"]=="offline"
    assert relay_failed["overall_state"]=="offline"
    assert next(row for row in relay_failed["sites"] if row["site_id"]==OFFICE)["state"]=="current"
    assert any(row.get("component")=="vp3_cloud_relay" for row in relay_failed["agent_context"]["active_issues"])
    relay["cloud"]={"state":"connected","connected":True,"paired":True,"transport":"vp3_https"}
    relay_recovered=health.build_report(operations,sync,access,relay,previous=relay_failed,now_ms=2000)
    assert relay_recovered["relay_health"]["state"]=="current"
    assert any(event["event_type"]=="relay.recovered" for event in relay_recovered["events"])

    operations,sync,access,relay=fixture()
    access["peers"][0]["policy_peer_allowed"]=False
    filtered=health.build_report(operations,sync,access,relay,previous={},now_ms=1000)
    assert any(row["site_id"]==OFFICE for row in filtered["sites"])
    assert all(row["site_id"]!=OFFICE for row in filtered["agent_context"]["sites"])
    assert all(row.get("site_id")!=OFFICE for row in filtered["agent_context"]["active_issues"])
    assert "Office" not in filtered["agent_context"]["summary"]
    assert filtered["agent_context"]["overall_state"]=="current"

    operations,sync,access,relay=fixture("reconciling",False)
    long_running=health.build_report(operations,sync,access,relay,previous={},now_ms=1000)
    escalated=health.build_report(operations,sync,access,relay,previous=long_running,now_ms=302000)
    office=next(row for row in escalated["sites"] if row["site_id"]==OFFICE)
    assert office["state"]=="reconciling"
    assert office["severity"]=="critical"
    assert any(event["event_type"]=="site.escalated" for event in escalated["events"])

    delivery={}
    event={"dedupe_key":"office|reconciling|x","severity":"warning"}
    delivered,reason=health._delivery_decision(event,delivery,1000)
    assert delivered is True and reason=="state_change"
    delivered,reason=health._delivery_decision(event,delivery,2000)
    assert delivered is False and reason=="dedupe_cooldown"
    delivered,reason=health._delivery_decision({**event,"severity":"critical"},delivery,2000)
    assert delivered is True and reason=="severity_escalated"

    cap=health.public_capability()
    assert cap["recovery_requires_authoritative_reconciliation"] is True
    assert cap["connectivity_returned_is_not_recovery"] is True
    assert cap["priority_agent_events"] is True
    assert cap["voice_eligible_events"] is True
    assert cap["persistent_operational_history"] is True
    assert cap["permission_filtered_agent_context"] is True
    assert cap["authority_mutation"] is False

    source=(ROOT/"app/services/tracky_federation_agent_health.py").read_text(encoding="utf-8")
    main=(ROOT/"app/main.py").read_text(encoding="utf-8")
    api=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
    agent=(ROOT/"app/services/tracky_federated_agent_context.py").read_text(encoding="utf-8")
    physical=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
    ambient=(ROOT/"app/services/ambient_agent.py").read_text(encoding="utf-8")
    index=(ROOT/"ui/index.html").read_text(encoding="utf-8")
    ui=(ROOT/"ui/federation-agent-health-v280.js").read_text(encoding="utf-8")
    spec=(ROOT/"HomeServer.spec").read_text(encoding="utf-8")

    assert "tracky_federation_agent_health.start()" in main
    assert "tracky_federation_agent_health.stop()" in main
    assert '/api/v1/control/federation-agent-health' in main
    assert '@router.get("/api/v1/tracky/federation-agent-health")' in api
    assert '@router.post("/api/v1/tracky/federation-agent-health' not in api
    assert '"federation_agent_health"' in agent
    assert "recovery_requires_authoritative_reconciliation" in agent
    assert '"federation_agent_health"' in physical
    assert "federation_agent_health_protocol" in physical
    assert "INSERT INTO notifications" in source
    assert "from . import remote_bridge" in source
    assert "remote_bridge," not in source.split(")\n\nTRACKY_FEDERATION_AGENT_HEALTH_VERSION",1)[0]
    assert 'source_app_key="tracky.federation.health"' in source
    assert "cognitive_runtime.list_events" in source
    assert "proactive_voice" in ambient
    assert "announcement_levels" in ambient
    assert "Recovery rule:" in index
    assert "reconnecting is not recovered" in index
    assert 'id="fahChatAlert"' in index
    assert 'aria-live="polite"' in index
    assert "federation-agent-health-v280.js" in index
    assert "federation-agent-health-v280.css" in index
    assert "renderChatAlert" in ui
    assert "window.setInterval" in ui
    assert "30000" in ui
    assert "recovered" in ui
    assert "method:'POST'" not in ui and 'method:"POST"' not in ui
    assert "method:'PUT'" not in ui and 'method:"PUT"' not in ui
    assert "('ui', 'ui')" in spec
    print("TRACKY_V280_FEDERATION_AGENT_HEALTH=PASS")


if __name__=="__main__":
    run()

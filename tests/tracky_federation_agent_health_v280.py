from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"
HOME_NODE="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OFFICE_NODE="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
T=2_000_000


def fixture(sync_status="current",previous=None):
    return {
        "operations":{
            "local_site_id":HOME,
            "sites":[
                {"id":HOME,"label":"Home","status":"active","health":"healthy","authority":{"status":"current","device_id":HOME_NODE,"epoch":4},"federation":{"status":"current"}},
                {"id":OFFICE,"label":"Office","status":"active","health":"healthy" if sync_status=="current" else "critical","authority":{"status":"current","device_id":OFFICE_NODE,"epoch":2},"federation":{"status":sync_status}},
            ],
            "devices":[
                {"id":HOME_NODE,"label":"Home Node","site_id":HOME,"hardware_profile":"node","runtime_status":"online"},
                {"id":OFFICE_NODE,"label":"Office Node","site_id":OFFICE,"hardware_profile":"node","runtime_status":"online"},
            ],
        },
        "sync":{
            "local_site_id":HOME,
            "sites":[
                {"site_id":HOME,"status":"current","fresh":True,"reconciliation_required":False},
                {"site_id":OFFICE,"status":sync_status,"fresh":sync_status=="current","reconciliation_required":sync_status!="current","last_error":"boom" if sync_status=="failed" else ""},
            ],
        },
        "access":{"peers":[{"site_id":OFFICE,"policy_peer_allowed":True,"federation_enabled":True}]},
        "bridge":{"state":"connected","connected":True,"paired":True,"transport":"vp3_https"},
        "previous":previous,
    }


def run():
    with tempfile.TemporaryDirectory(prefix="tracky-v280-s7-") as data_dir:
        os.environ["HOMESERVER_DATA_DIR"]=data_dir
        os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"

        from app.database import db, initialize_database
        from app.services import tracky_federation_agent_health as health

        initialize_database()

        data=fixture("partitioned")
        partitioned=health.build_report(
            data["operations"],data["sync"],data["access"],data["bridge"],
            previous=data["previous"],now_ms=T,
        )
        assert partitioned["protocol"]==health.FEDERATION_AGENT_HEALTH_PROTOCOL
        office=next(row for row in partitioned["sites"] if row["site_id"]==OFFICE)
        assert office["state"]=="partitioned"
        assert office["fresh"] is False
        assert office["trust"]["agent_may_treat_remote_state_as_current"] is False
        event=next(row for row in partitioned["events"] if row["site_id"]==OFFICE)
        assert event["event_type"]=="site_partitioned"
        assert event["notification"] is True
        assert event["chat"] is True

        data=fixture("reconciling",partitioned)
        recovering=health.build_report(
            data["operations"],data["sync"],data["access"],data["bridge"],
            previous=data["previous"],now_ms=T+1000,
        )
        office=next(row for row in recovering["sites"] if row["site_id"]==OFFICE)
        assert office["state"]=="recovering"
        assert office["recovery_pending"] is True
        assert any(row["event_type"]=="site_recovering" for row in recovering["events"])
        assert not any(row["event_type"]=="site_recovered" for row in recovering["events"])

        data=fixture("current",recovering)
        recovered=health.build_report(
            data["operations"],data["sync"],data["access"],data["bridge"],
            previous=data["previous"],now_ms=T+2000,
        )
        office=next(row for row in recovered["sites"] if row["site_id"]==OFFICE)
        assert office["state"]=="connected"
        recovery_event=next(row for row in recovered["events"] if row["event_type"]=="site_recovered")
        assert recovery_event["recovery_complete"] is True
        assert recovery_event["reconciliation_required"] is False

        data=fixture("partitioned")
        first=health.build_report(data["operations"],data["sync"],data["access"],data["bridge"],now_ms=T)
        data=fixture("partitioned",first)
        same=health.build_report(data["operations"],data["sync"],data["access"],data["bridge"],previous=first,now_ms=T+30_000)
        assert not any(row["site_id"]==OFFICE for row in same["events"])
        data=fixture("partitioned",same)
        escalated=health.build_report(data["operations"],data["sync"],data["access"],data["bridge"],previous=same,now_ms=T+16*60_000)
        assert any(row["event_type"]=="site_health_escalated" and row["site_id"]==OFFICE for row in escalated["events"])

        data=fixture("current")
        data["bridge"]={"state":"offline","connected":False,"paired":True,"last_error":"relay unavailable","transport":"vp3_https"}
        relay=health.build_report(data["operations"],data["sync"],data["access"],data["bridge"],now_ms=T)
        home=next(row for row in relay["sites"] if row["site_id"]==HOME)
        assert home["trust"]["local_physical_truth_current"] is True
        assert home["trust"]["cloud_transport_connected"] is False
        relay_event=next(row for row in relay["events"] if row["event_type"]=="relay_disconnected")
        assert "Local physical truth may remain available" in relay_event["body"]

        data=fixture("current")
        data["bridge"]={"state":"not_connected","connected":False,"paired":False,"transport":"vp3_https"}
        unpaired=health.build_report(data["operations"],data["sync"],data["access"],data["bridge"],now_ms=T)
        assert not any(row["event_type"].startswith("relay_") for row in unpaired["events"])

        data=fixture("current")
        data["operations"]["devices"][1]["runtime_status"]="offline"
        authority_offline=health.build_report(data["operations"],data["sync"],data["access"],data["bridge"],now_ms=T)
        office=next(row for row in authority_offline["sites"] if row["site_id"]==OFFICE)
        assert office["state"]=="offline"
        assert office["issue_code"]=="authority_device_offline"

        delivered={
            "event_type":"site_partitioned",
            "site_id":OFFICE,
            "site_label":"Office",
            "previous_state":"connected",
            "state":"partitioned",
            "priority":"high",
            "title":"Office partitioned",
            "body":"Office is partitioned. Remote physical data must be treated as stale.",
            "issue_code":"federation_partitioned",
            "occurred_at":T+50_000,
            "chat":True,
            "notification":True,
            "voice_eligible":True,
            "recovery_complete":False,
            "reconciliation_required":True,
            "dedupe_key":"site:"+OFFICE+":site_partitioned:0",
        }
        health._deliver(delivered)
        health._deliver(delivered)
        with db() as connection:
            cognitive=connection.execute(
                "SELECT COUNT(*) AS n FROM cognitive_events WHERE source_app_key='tracky' AND event_type='tracky.federation_health.site_partitioned'"
            ).fetchone()["n"]
            notes=connection.execute(
                "SELECT COUNT(*) AS n FROM notifications WHERE source='tracky' AND title='Office partitioned'"
            ).fetchone()["n"]
            conv=connection.execute(
                "SELECT id FROM conversations WHERE title='Federation Health' AND source_app_key='owner'"
            ).fetchone()
            assert conv is not None
            messages=connection.execute(
                "SELECT COUNT(*) AS n FROM conversation_messages WHERE conversation_id=? AND role='assistant'",
                (conv["id"],),
            ).fetchone()["n"]
        assert cognitive==1
        assert notes==1
        assert messages==1

        history=health.history(10)
        assert len(history)==1
        assert history[0]["immutable"] is True
        assert history[0]["payload"]["voice_eligible"] is True
        assert history[0]["payload"]["reconnect_is_not_recovery"] is True

        cap=health.public_capability()
        assert cap["recovery_requires_current_reconciliation"] is True
        assert cap["duplicate_state_suppression"] is True
        assert cap["duration_escalation"] is True
        assert cap["voice_respects_existing_settings"] is True
        assert cap["authority_mutation"] is False

        main_source=(ROOT/"app/main.py").read_text(encoding="utf-8")
        paired_source=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
        agent_source=(ROOT/"app/services/tracky_federated_agent_context.py").read_text(encoding="utf-8")
        context_source=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
        ambient_source=(ROOT/"app/services/ambient_agent.py").read_text(encoding="utf-8")
        ui=(ROOT/"ui/federation-agent-health-v280.js").read_text(encoding="utf-8")
        index=(ROOT/"ui/index.html").read_text(encoding="utf-8")
        spec=(ROOT/"HomeServer.spec").read_text(encoding="utf-8")

        assert "tracky_federation_agent_health.start()" in main_source
        assert "tracky_federation_agent_health.stop()" in main_source
        assert '/api/v1/control/federation-agent-health' in main_source
        assert '@router.get("/api/v1/tracky/federation-agent-health")' in paired_source
        assert '@router.post("/api/v1/tracky/federation-agent-health' not in paired_source
        assert '"federation_agent_health"' in agent_source
        assert '"federation_agent_health"' in context_source
        assert "federation_agent_health_protocol" in context_source
        assert "tasks.list_notifications" in ambient_source
        assert 'settings["proactive_voice"]' in ambient_source
        assert "setInterval" in ui
        assert "Recovery rule:" in index
        assert "federation-agent-health-v280.js" in index
        assert "federation-agent-health-v280.css" in index
        assert "('ui', 'ui')" in spec

        print("TRACKY_V280_AGENT_FEDERATION_HEALTH=PASS")


if __name__=="__main__":
    run()

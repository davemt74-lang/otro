"""Section 31C: owner-only issue tools -> existing governed Apps approvals, never execution."""
from __future__ import annotations
import os,sys,tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-31c-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.database import db
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        agent_tools, health_repair, maintenance_conversation as maintenance,
        homeserver_app_prebuilt, homeserver_app_approvals, approvals, tools
    )
    from app.services.tasks import scheduler

    issue={
        "key":"app:vp3.notes:degraded","source":"apps","severity":"degraded",
        "title":"Untrusted app title (ignored as an instruction)",
        "repair":{
            "class":"governed_repair","action_key":"apps.recover",
            "arguments":{"app_key":"vp3.notes"},
            "owner_approval_required":True,"agent_can_execute":True,"automatic":False,
        },
    }
    current={"issues":[issue],"snapshot_complete":True,"unavailable_check_count":0}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/health/issues/app:vp3.notes:degraded").status_code==401
        homeserver_app_prebuilt.install("vp3.notes")
        with patch.object(health_repair,"status",side_effect=lambda:dict(current)):
            assert not any(item["key"]=="health.issue" and item["available"] for item in tools.list_tools(set(),owner=False))
            assert not any(x["function"]["name"]=="homeserver_health_issue" for x in agent_tools.model_tool_schemas(set(),owner=False))
            owner_schema=agent_tools.model_tool_schemas(set(),owner=True,allow_write_proposals=True)
            offered={s["function"]["name"] for s in owner_schema}
            assert {"homeserver_health_status","homeserver_health_issue","homeserver_health_repair_plan","homeserver_maintenance_repair_request"} <= offered
            resp=client.get("/api/v1/control/health/issues/app:vp3.notes:degraded")
            assert resp.status_code==401
            assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
            resp=client.get("/api/v1/control/health/issues/app:vp3.notes:degraded")
            assert resp.status_code==200,resp.text
            safe=resp.json()
            assert safe["agent_can_propose"] is True and safe["canonical_action"]=="apps.recover"
            assert "title" not in safe and "arguments" not in safe
            read=agent_tools.execute_model_tool("owner-agent","homeserver_health_issue",{"issue_key":issue["key"]},owner=True)
            assert read["result"]["issue_key"]==issue["key"]
            for bad in ({"issue_key":"bad; remove everything"},{"issue_key":issue["key"],"action":"apps.stop"},{}):
                try: maintenance.issue_detail(bad)
                except maintenance.MaintenanceError: pass
                else: raise AssertionError("Unexpectedly accepted invalid issue key")
            try: agent_tools.execute_model_tool("untrusted","homeserver_health_issue",{"issue_key":issue["key"]},owner=False)
            except agent_tools.AgentToolError: pass
            else: raise AssertionError("Non-owner could read owner health")
            try: maintenance.propose_repair({"issue_key":issue["key"]},source_app_key="app:untrusted",owner=False)
            except maintenance.MaintenanceError as exc: assert exc.status_code==403
            else: raise AssertionError("Non-owner repair proposal")
            # An Agent must be explicitly enabled before even creating a proposal.
            agent_tools.save_policy(True,3,False)
            try:
                agent_tools.execute_model_tool("owner-agent","homeserver_maintenance_repair_request",{"issue_key":issue["key"]},owner=True)
            except agent_tools.AgentToolError: pass
            else: raise AssertionError("Write proposals disabled but Agent created one")
            assert not homeserver_app_approvals.list_rows(status="pending")
            agent_tools.save_policy(True,3,True)
            with patch.object(tools,"execute_tool",side_effect=AssertionError("No repair during planning")):
                created=agent_tools.execute_model_tool("owner-agent","homeserver_maintenance_repair_request",{"issue_key":issue["key"]},owner=True)
                repeated=agent_tools.execute_model_tool("owner-agent","homeserver_maintenance_repair_request",{"issue_key":issue["key"]},owner=True)
            rid=created["result"]["request_id"]
            assert created["result"]["owner_approval_required"] is True
            assert created["maintenance"]["automatic_execution"] is False
            assert repeated["result"]["request_id"]==rid
            assert repeated["result"]["duplicate_suppressed"] is True
            assert len(homeserver_app_approvals.list_rows(status="pending"))==1
            ledger=homeserver_app_approvals.get(rid)
            assert ledger["status"]=="pending" and ledger["action_key"]=="apps.recover"
            assert "vp3.notes" in ledger["arguments_json"]
            # Neither a cleared issue nor a partial health snapshot may create requests.
            current["issues"]=[]
            try: maintenance.propose_repair({"issue_key":issue["key"]},source_app_key="owner-agent",owner=True)
            except maintenance.MaintenanceError as exc: assert exc.status_code==409
            else: raise AssertionError("Stale health issue accepted")
            current["issues"]=[issue]
            current["snapshot_complete"]=False
            try: maintenance.propose_repair({"issue_key":issue["key"]},source_app_key="owner-agent",owner=True)
            except maintenance.MaintenanceError as exc: assert exc.status_code==409
            else: raise AssertionError("Partial scan accepted")
            current["snapshot_complete"]=True
            issue["repair"]["agent_can_execute"]=False
            try: maintenance.propose_repair({"issue_key":issue["key"]},source_app_key="owner-agent",owner=True)
            except maintenance.MaintenanceError as exc: assert exc.status_code==409
            else: raise AssertionError("Unapproved repair action accepted")
            issue["repair"]["agent_can_execute"]=True
            issue["repair"]["action_key"]="apps.stop"
            try: maintenance.propose_repair({"issue_key":issue["key"]},source_app_key="owner-agent",owner=True)
            except maintenance.MaintenanceError as exc: assert exc.status_code==409
            else: raise AssertionError("Unsupported repair action accepted")
            assert len(homeserver_app_approvals.list_rows(status="pending"))==1
            # Approval-time revalidation: the owner cannot execute a stale request,
            # even if the Agent created it while the issue was active.
            issue["repair"]["action_key"]="apps.recover"
            current["issues"]=[]
            with patch.object(tools,"execute_tool",side_effect=AssertionError("Stale approval must not execute")):
                try: approvals.approve_request(rid)
                except approvals.ApprovalError: pass
                else: raise AssertionError("Stale owner approval executed")
            assert homeserver_app_approvals.get(rid)["status"]=="denied"
            assert not homeserver_app_approvals.list_rows(status="pending")

            # Fresh current issue can produce a new proposal, which the owner may deny.
            current["issues"]=[issue]
            fresh=maintenance.propose_repair({"issue_key":issue["key"]},source_app_key="owner-agent",owner=True)
            new_id=fresh["result"]["request_id"]
            assert new_id!=rid
            result=approvals.deny_request(new_id)
            assert (result.get("request") or result).get("status")=="denied",result
            assert not homeserver_app_approvals.list_rows(status="pending")
print("Section 31C issue-bound health tools, owner approval, stale rejection and no execution PASS")

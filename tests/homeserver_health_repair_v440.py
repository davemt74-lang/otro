from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-health-v440-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        health_repair,
        homeserver_app_agent,
        homeserver_app_prebuilt,
        storage_maintenance,
        tools,
    )
    from app.services.tasks import scheduler as task_scheduler

    with TestClient(app) as client:
        task_scheduler.stop()

        assert client.get("/api/v1/control/health").status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        homeserver_app_prebuilt.install("vp3.notes")
        with db() as connection:
            connection.execute(
                "UPDATE homeserver_apps SET lifecycle_state='degraded' WHERE app_key='vp3.notes'"
            )

        # Also create a low-space signal without actually filling disk.
        policy=storage_maintenance.policy()
        storage_maintenance.update_policy({
            "minimum_free_bytes":1099511627776,
            "warning_free_percent":policy["warning_free_percent"],
            "critical_free_percent":policy["critical_free_percent"],
            "allow_owner_backup_prune":policy["allow_owner_backup_prune"],
        })

        status=client.get("/api/v1/control/health")
        assert status.status_code==200,status.text
        body=status.json()
        assert body["contract"]=="vp3.homeserver.health-repair.v1"
        assert body["overall"] in {"failed","degraded","attention"}
        assert body["governance"]["automatic_repair"] is False
        assert body["governance"]["invented_repairs_allowed"] is False
        assert body["governance"]["canonical_actions_only"] is True
        assert body["governance"]["owner_approval_preserved"] is True

        app_issue=next(item for item in body["issues"] if item["key"]=="app:vp3.notes:degraded")
        assert app_issue["repair"]["class"]=="governed_repair"
        assert app_issue["repair"]["action_key"]=="apps.recover"
        assert app_issue["repair"]["arguments"]=={"app_key":"vp3.notes"}
        assert app_issue["repair"]["owner_approval_required"] is True
        assert app_issue["repair"]["agent_can_execute"] is True
        assert app_issue["repair"]["automatic"] is False

        storage_issue=next(item for item in body["issues"] if item["key"]=="storage:disk-pressure")
        assert storage_issue["repair"]["agent_can_execute"] is False
        assert storage_issue["repair"]["action_key"] is None

        plan=client.get("/api/v1/control/health/repair-plan")
        assert plan.status_code==200,plan.text
        plan_body=plan.json()
        assert plan_body["automatic_execution"] is False
        assert any(item["repair"]["action_key"]=="apps.recover" for item in plan_body["items"])

        # Diagnosis and planning must not repair the app implicitly.
        with db() as connection:
            state=connection.execute(
                "SELECT lifecycle_state FROM homeserver_apps WHERE app_key='vp3.notes'"
            ).fetchone()[0]
        assert state=="degraded"

        brain=client.get("/api/v1/control/health/brain-context")
        assert brain.status_code==200,brain.text
        brain_payload=brain.json()
        assert brain_payload["contract"]=="vp3.homeserver.health-repair.brain-context.v1"
        assert brain_payload["agent_repairable_count"]>=1
        assert brain_payload["governance"]["automatic_repair"] is False

        fragment=health_repair.agent_context_fragment("What is wrong with my HomeServer?",1700)
        assert "HomeServer health and repair context" in fragment
        assert "apps.recover" in fragment
        assert "automatic_repair=false" in fragment
        assert "canonical_actions_only=true" in fragment

        owner_tools={row["key"]:row for row in tools.list_tools(owner=True)}
        assert owner_tools["health.status"]["available"] is True
        assert owner_tools["health.repair-plan"]["available"] is True
        owner_skills={row["key"]:row for row in tools.list_skills(owner=True)}
        assert owner_skills["homeserver.health"]["available"] is True

        app_tools={row["key"]:row for row in tools.list_tools({"tools.execute","apps.read"},owner=False)}
        assert app_tools["health.status"]["available"] is False
        assert app_tools["health.repair-plan"]["available"] is False
        assert "owner.control" in app_tools["health.status"]["missing_permissions"]

        health_tool=tools.execute_tool("owner","health.status",{},set(),owner=True)
        assert health_tool["status"]=="completed"
        assert health_tool["result"]["governance"]["automatic_repair"] is False
        repair_tool=tools.execute_tool("owner","health.repair-plan",{},set(),owner=True)
        assert repair_tool["status"]=="completed"
        assert any(
            item["repair"]["action_key"]=="apps.recover"
            for item in repair_tool["result"]["items"]
        )

        # Existing app Agent action remains the only repair execution authority.
        capability=homeserver_app_agent.public_capability()
        assert "apps.recover" in capability["write_actions"]
        assert capability["write_actions_require_owner_approval"] is True
        normalized=homeserver_app_agent.normalize_action("apps.recover",{"app_key":"vp3.notes"})
        assert normalized=={"app_key":"vp3.notes"}

        # Read-only Health tools still did not alter app state.
        with db() as connection:
            state=connection.execute(
                "SELECT lifecycle_state FROM homeserver_apps WHERE app_key='vp3.notes'"
            ).fetchone()[0]
        assert state=="degraded"

        cap=client.get("/api/v1/control/health/capability")
        assert cap.status_code==200,cap.text
        capability=cap.json()
        assert capability["unified_health"] is True
        assert capability["repair_planning"] is True
        assert capability["canonical_actions_only"] is True
        assert capability["automatic_repair"] is False
        assert capability["owner_approval_preserved"] is True
        assert capability["agent_brain_context"] is True
        assert capability["agent_chat_context"] is True

        serialized=json.dumps(body,ensure_ascii=False)
        assert str(Path(data_dir)) not in serialized

        ui=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
        css=(ROOT/"ui"/"health.css").read_text(encoding="utf-8")
        assert "view-health" in ui
        assert "What the Agent can actually fix" in ui
        assert "/api/v1/control/health/repair-plan" in ui
        assert "No automatic repair" in ui
        assert ".health-summary" in css

print("HomeServer Section 30 Unified Health & Repair Orchestration: PASS")

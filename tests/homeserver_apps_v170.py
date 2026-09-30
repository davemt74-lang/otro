from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v170-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database  # noqa: E402
    from app.services import agent_tools, approvals, homeserver_app_agent, homeserver_apps  # noqa: E402

    initialize_database()
    policy=agent_tools.save_policy(True,3,True)
    assert policy["enabled"] is True
    assert policy["allow_write_proposals"] is True

    created=homeserver_apps.create_user_app("agent.demo","Agent Demo",runtime="static")
    assert created["app"]["lifecycle_state"]=="draft"

    owner_schemas=agent_tools.model_tool_schemas(
        set(),owner=True,allow_write_proposals=True,source_app_key="owner"
    )
    names={item["function"]["name"] for item in owner_schemas}
    assert "homeserver_apps_list" in names
    assert "homeserver_app_get" in names
    assert "homeserver_app_releases" in names
    assert "homeserver_app_build_install_request" in names
    assert "homeserver_app_stop_request" in names
    assert "homeserver_app_rollback_request" in names

    paired_schemas=agent_tools.model_tool_schemas(
        {"apps.read","apps.manage","tools.execute"},
        owner=False,allow_write_proposals=True,source_app_key="app:external"
    )
    paired_names={item["function"]["name"] for item in paired_schemas}
    assert "homeserver_app_build_install_request" not in paired_names
    assert "homeserver_app_stop_request" not in paired_names

    listed=agent_tools.execute_model_tool(
        "owner","homeserver_apps_list",{},set(),owner=True
    )
    assert listed["status"]=="completed"
    assert listed["result"]["count"]>=1
    assert any(row["app_key"]=="agent.demo" for row in listed["result"]["items"])

    detail=agent_tools.execute_model_tool(
        "owner","homeserver_app_get",{"app_key":"agent.demo"},set(),owner=True
    )
    assert detail["result"]["app"]["app_key"]=="agent.demo"
    assert detail["result"]["app"]["lifecycle_state"]=="draft"

    proposal=agent_tools.execute_model_tool(
        "owner",
        "homeserver_app_build_install_request",
        {"app_key":"agent.demo"},
        set(),
        owner=True,
    )
    request_id=proposal["result"]["request_id"]
    assert proposal["result"]["status"]=="pending"
    assert homeserver_apps.get("agent.demo")["lifecycle_state"]=="draft"

    approved=approvals.approve_request(request_id)
    assert approved["status"]=="executed"
    assert homeserver_apps.get("agent.demo")["lifecycle_state"]=="running"

    stop=agent_tools.execute_model_tool(
        "owner",
        "homeserver_app_stop_request",
        {"app_key":"agent.demo"},
        set(),
        owner=True,
    )
    stop_request=stop["result"]["request_id"]
    assert homeserver_apps.get("agent.demo")["lifecycle_state"]=="running"
    approvals.approve_request(stop_request)
    assert homeserver_apps.get("agent.demo")["lifecycle_state"]=="stopped"

    start=agent_tools.execute_model_tool(
        "owner",
        "homeserver_app_start_request",
        {"app_key":"agent.demo"},
        set(),
        owner=True,
    )
    approvals.approve_request(start["result"]["request_id"])
    assert homeserver_apps.get("agent.demo")["lifecycle_state"]=="running"

    prebuilt=agent_tools.execute_model_tool(
        "owner",
        "homeserver_app_install_prebuilt_request",
        {"app_key":"vp3.notes"},
        set(),
        owner=True,
    )
    assert prebuilt["result"]["status"]=="pending"
    approvals.approve_request(prebuilt["result"]["request_id"])
    notes=homeserver_apps.get("vp3.notes")
    assert notes["app_class"]=="system"
    assert notes["protected_system_app"] is True

    releases=agent_tools.execute_model_tool(
        "owner",
        "homeserver_app_releases",
        {"app_key":"agent.demo","limit":5},
        set(),
        owner=True,
    )
    assert releases["result"]["count"]>=1
    assert releases["result"]["active_release_id"]

    try:
        approvals.create_app_action_request(
            "app:external","apps.stop",{"app_key":"agent.demo"},owner=False
        )
        raise AssertionError("paired app was allowed to administer HomeServer Apps")
    except approvals.ApprovalError as exc:
        assert exc.status_code==403

    cap=homeserver_app_agent.public_capability()
    assert cap["agent_brain_context"] is True
    assert cap["agent_chat_tools"] is True
    assert cap["write_actions_require_owner_approval"] is True
    assert cap["paired_app_admin_tools"] is False
    assert cap["system_app_protection"] is True

print("HomeServer Apps V1 Section 8 Agent Chat and Agent Brain integration: PASS")

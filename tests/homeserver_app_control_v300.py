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

with tempfile.TemporaryDirectory(prefix="homeserver-app-control-v300-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        approvals,
        homeserver_app_agent,
        homeserver_app_control,
        homeserver_app_manager,
        homeserver_app_runtime,
        homeserver_app_security,
        homeserver_apps,
        tools,
    )
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"control.demo",
            "name":"Universal Control Demo",
            "runtime":"static",
            "source_type":"agent_builder",
            "permissions":["notifications.write"],
        })
        assert created.status_code==200,created.text
        assert created.json()["sdk"]["sdk_version"]=="1.2"

        project=Path(data_dir)/"apps"/"control.demo"
        manifest=json.loads((project/"vp3-app.json").read_text(encoding="utf-8"))
        assert manifest["sdk_version"]=="1.2"
        assert manifest["agent_actions"]=="agent/actions.json"

        settings_schema={
            "contract":"vp3.app.settings-schema.v1",
            "fields":[
                {"key":"theme","type":"string","default":"system","enum":["system","light","dark"]},
                {"key":"max_items","type":"integer","default":25,"minimum":1,"maximum":100},
                {"key":"api_token","type":"string","secret":True},
            ],
        }
        (project/"settings.schema.json").write_text(json.dumps(settings_schema,indent=2)+"\n",encoding="utf-8")

        actions={
            "contract":"vp3.app.agent-actions.v2",
            "actions":[
                {
                    "key":"demo.status",
                    "name":"Read runtime status",
                    "risk":"read",
                    "requires_confirmation":False,
                    "input_schema":{"type":"object","properties":{},"additionalProperties":False},
                    "executor":{"type":"runtime.status"},
                },
                {
                    "key":"demo.settings",
                    "name":"Read settings",
                    "risk":"read",
                    "requires_confirmation":False,
                    "input_schema":{"type":"object","properties":{},"additionalProperties":False},
                    "executor":{"type":"settings.read"},
                },
                {
                    "key":"demo.refresh",
                    "name":"Refresh demo state",
                    "risk":"background",
                    "requires_confirmation":False,
                    "input_schema":{"type":"object","properties":{"reason":{"type":"string"}},"additionalProperties":False},
                    "executor":{"type":"event.emit","topic":"demo.refresh","payload":{"source":"agent"}},
                },
                {
                    "key":"demo.reset",
                    "name":"Reset demo state",
                    "risk":"destructive",
                    "requires_confirmation":True,
                    "input_schema":{"type":"object","properties":{"reason":{"type":"string"}},"additionalProperties":False},
                    "executor":{"type":"event.emit","topic":"demo.reset"},
                },
            ],
        }
        (project/"agent"/"actions.json").write_text(json.dumps(actions,indent=2)+"\n",encoding="utf-8")

        installed=client.post("/api/v1/control/homeserver-apps/control.demo/build-install")
        assert installed.status_code==200,installed.text
        assert installed.json()["release"]["version"]=="0.1.0"

        # Universal compatibility and manifest discovery are independent of app-specific code.
        compatibility=client.get("/api/v1/control/homeserver-apps/control.demo/control")
        assert compatibility.status_code==200,compatibility.text
        cp=compatibility.json()
        assert cp["compatibility"]["compatible"] is True
        assert cp["compatibility"]["manifest_contract"]=="vp3.app.agent-actions.v2"
        assert len(cp["manifest"]["actions"])==4
        assert cp["hosting"]["count"]==0

        manager=client.get("/api/v1/control/homeserver-apps/manager/control.demo")
        assert manager.status_code==200,manager.text
        agent_control=manager.json()["app"]["agent_control"]
        assert agent_control["complete"] is True
        assert agent_control["compatible"] is True
        assert agent_control["manifest"]["count"]==4

        # Settings are schema-driven. Secret values are write-only and never projected.
        before=client.get("/api/v1/control/homeserver-apps/control.demo/settings")
        assert before.status_code==200,before.text
        assert before.json()["values"]["theme"]=="system"
        assert before.json()["values"]["max_items"]==25
        assert before.json()["values"]["api_token"]=={"configured":False,"secret":True}

        secret="section16-secret-value"
        updated=client.put("/api/v1/control/homeserver-apps/control.demo/settings",json={
            "values":{"theme":"dark","max_items":50,"api_token":secret}
        })
        assert updated.status_code==200,updated.text
        assert secret not in updated.text
        values=updated.json()["values"]
        assert values["theme"]=="dark"
        assert values["max_items"]==50
        assert values["api_token"]=={"configured":True,"secret":True}
        assert homeserver_app_security.get_secret("control.demo","API_TOKEN")==secret
        assert secret not in json.dumps(homeserver_apps.get("control.demo"),sort_keys=True)

        bad=client.put("/api/v1/control/homeserver-apps/control.demo/settings",json={"values":{"max_items":101}})
        assert bad.status_code==400

        # Read actions use genuinely read-only generic executors.
        read=client.post("/api/v1/control/homeserver-apps/control.demo/control/invoke",json={
            "action":"demo.status","arguments":{}
        })
        assert read.status_code==200,read.text
        assert read.json()["result"]["contract"]=="vp3.app.runtime-services.v1"

        settings_read=homeserver_app_agent.invoke_read({
            "app_key":"control.demo","action":"demo.settings","arguments":{}
        })
        assert settings_read["result"]["values"]["theme"]=="dark"
        assert secret not in json.dumps(settings_read)

        # Non-destructive app actions use the same generic manifest router.
        refresh=client.post("/api/v1/control/homeserver-apps/control.demo/control/invoke",json={
            "action":"demo.refresh","arguments":{"reason":"test"}
        })
        assert refresh.status_code==200,refresh.text
        events=homeserver_app_runtime.list_events("control.demo",topic="demo.refresh")
        assert events and events[0]["payload"]["reason"]=="test"
        assert events[0]["payload"]["source"]=="agent"

        # Destructive actions fail closed until explicitly confirmed.
        blocked=client.post("/api/v1/control/homeserver-apps/control.demo/control/invoke",json={
            "action":"demo.reset","arguments":{"reason":"owner-test"},"confirmed":False
        })
        assert blocked.status_code==409

        confirmed=client.post("/api/v1/control/homeserver-apps/control.demo/control/invoke",json={
            "action":"demo.reset","arguments":{"reason":"owner-test"},"confirmed":True
        })
        assert confirmed.status_code==200,confirmed.text
        assert homeserver_app_runtime.list_events("control.demo",topic="demo.reset")

        # The Agent approval ledger can execute the same generic action without app-specific approval code.
        request=approvals.create_app_action_request(
            "homeserver-agent",
            "apps.invoke",
            {"app_key":"control.demo","action":"demo.reset","arguments":{"reason":"approved-ledger"}},
            owner=True,
        )
        request_id=request["result"]["request_id"]
        approved=approvals.approve_request(request_id)
        assert approved["status"]=="executed"
        resets=homeserver_app_runtime.list_events("control.demo",topic="demo.reset")
        assert any(row["payload"].get("reason")=="approved-ledger" for row in resets)

        # Settings writes also use the same approval/action ledger.
        setting_request=approvals.create_app_action_request(
            "homeserver-agent",
            "apps.settings.set",
            {"app_key":"control.demo","values":{"theme":"light"}},
            owner=True,
        )
        assert approvals.approve_request(setting_request["result"]["request_id"])["status"]=="executed"
        assert homeserver_app_control.settings("control.demo")["values"]["theme"]=="light"

        # Complete lifecycle control remains canonical for user apps.
        stopped=homeserver_app_agent.execute_action("apps.stop",{"app_key":"control.demo"})
        assert stopped["app"]["lifecycle_state"]=="stopped"
        started=homeserver_app_agent.execute_action("apps.start",{"app_key":"control.demo"})
        assert started["app"]["lifecycle_state"]=="running"

        # All generic controls are present in the owner tool catalog.
        tool_keys={row["key"] for row in tools.list_tools(owner=True)}
        assert {
            "apps.actions","apps.compatibility","apps.settings.get","apps.settings.set",
            "apps.hosting.status","apps.invoke.read","apps.invoke","apps.permission.set",
            "apps.start","apps.stop","apps.rollback","apps.recover",
        }.issubset(tool_keys)

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()
        assert cap["control"]["contract"]=="vp3.app.agent-control.v3"
        assert cap["control"]["generic_action_discovery"] is True
        assert cap["control"]["generic_invocation"] is True
        assert cap["control"]["generic_settings_control"] is True
        assert cap["control"]["compatibility_negotiation"] is True
        assert cap["manager"]["compatibility_status"] is True
        assert cap["agent"]["universal_app_control_contract"]=="vp3.app.agent-control.v3"

print("HomeServer Section 16 Universal App Control Contract: PASS")

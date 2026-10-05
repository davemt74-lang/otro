from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def rejects(operation,error):
    try:
        operation()
    except error:
        return
    raise AssertionError("Operation unexpectedly succeeded")


with tempfile.TemporaryDirectory(prefix="homeserver-section16-") as directory:
    os.environ["HOMESERVER_DATA_DIR"]=directory
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.database import db
    from app.services import (
        homeserver_app_agent as agent, homeserver_app_control as control,
        homeserver_app_packages as packages, homeserver_app_releases as releases,
        homeserver_app_resources as resources, homeserver_app_runtime as runtime,
        homeserver_app_security as security, homeserver_apps as apps, plugins,
    )
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        key="section16.demo"
        response=client.post("/api/v1/control/homeserver-apps",json={"app_key":key,"name":"Section16 Demo","runtime":"static"})
        assert response.status_code==200,response.text
        project=Path(directory)/"apps"/key
        schema={"contract":"vp3.app.settings-schema.v1","fields":[
            {"key":"api_token","type":"string","secret":True},
            {"key":"theme","type":"string","default":"light"},
            {"key":"limit","type":"integer","minimum":1,"maximum":100,"default":10},
            {"key":"ratio","type":"number","default":1},
        ]}
        action={"key":"demo.status","risk":"read","input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"runtime.status"}}
        reset={"key":"demo.reset","risk":"destructive","input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"event.emit","topic":"demo.reset"}}
        actions={"contract":"vp3.app.agent-actions.v2","actions":[action,reset]}
        manifest=json.loads((project/"vp3-app.json").read_text(encoding="utf-8"))
        manifest.update({"settings_schema":"settings.schema.json","agent_actions":"agent/actions.json"})
        (project/"vp3-app.json").write_text(json.dumps(manifest),encoding="utf-8")
        (project/"settings.schema.json").write_text(json.dumps(schema),encoding="utf-8")
        (project/"agent/actions.json").write_text(json.dumps(actions),encoding="utf-8")
        first=packages.install_project(key)
        control.update_settings(key,{"api_token":"original-secret","theme":"dark"})
        old_vault=security._vault_path(apps.get(key)["app_id"]).read_bytes()
        old_values=control.settings(key)["values"]
        old_events=len(apps.history(key))
        rejects(lambda:control.update_settings(key,{"api_token":"replacement-secret","theme":"new","limit":101}),control.AppControlError)
        assert security._vault_path(apps.get(key)["app_id"]).read_bytes()==old_vault
        assert control.settings(key)["values"]==old_values
        assert len(apps.history(key))==old_events
        for number in (float("nan"),float("inf"),-float("inf")):
            rejects(lambda:control.update_settings(key,{"ratio":number}),control.AppControlError)

        # A database failure after the vault write restores both persisted stores.
        from contextlib import contextmanager
        @contextmanager
        def failed_commit():
            with db() as connection:
                yield connection
                raise RuntimeError("injected database commit failure")
        with patch.object(control,"db",failed_commit):
            rejects(lambda:control.update_settings(key,{"api_token":"rollback-secret","theme":"rollback"}),RuntimeError)
        assert security._vault_path(apps.get(key)["app_id"]).read_bytes()==old_vault
        assert control.settings(key)["values"]==old_values
        assert len(apps.history(key))==old_events
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(control.update_settings,key,{"theme":"concurrent"}),executor.submit(control.update_settings,key,{"limit":77})]
            for future in futures:
                future.result(timeout=20)
        values=control.settings(key)["values"]
        assert values["theme"]=="concurrent" and values["limit"]==77
        assert "original-secret" not in json.dumps(apps.history(key))
        collision=copy.deepcopy(schema)
        collision["fields"]=[{"key":"api-token","type":"string","secret":True},{"key":"api_token","type":"string","secret":True}]
        (project/"collision.json").write_text(json.dumps(collision),encoding="utf-8")
        rejects(lambda:control.validate_settings_schema(project,key,"collision.json"),control.AppControlError)

        # Confirmation is enforced inside the canonical dispatcher.
        rejects(lambda:control.invoke(key,"demo.reset",{}),control.AppControlError)
        rejects(lambda:control.invoke(key,"demo.reset",{},confirmed="false"),control.AppControlError)
        control.invoke(key,"demo.reset",{},confirmed=True)
        before=len(runtime.list_events(key,topic="demo.reset"))
        stale_read=copy.deepcopy(reset)
        stale_read.update({"risk":"read","requires_confirmation":False})
        actual=control.action_spec(key,"demo.reset")
        with patch.object(control,"action_spec",side_effect=[stale_read,actual]):
            rejects(lambda:agent.invoke_read({"app_key":key,"action":"demo.reset"}),agent.AppAgentError)
        assert len(runtime.list_events(key,topic="demo.reset"))==before
        rejects(lambda:agent.normalize_action("apps.permission.set",{"app_key":key,"permission":"notifications.write","allowed":"false"}),agent.AppAgentError)

        # Approved actions are bound to the release and validated action definition.
        approved=agent.normalize_action("apps.invoke",{"app_key":key,"action":"demo.reset","arguments":{}})
        assert agent.normalize_action("apps.invoke",approved)==approved
        manifest.update({"version":"0.2.0","agent_actions":"agent/second.json","settings_schema":"second-settings.json"})
        (project/"vp3-app.json").write_text(json.dumps(manifest),encoding="utf-8")
        (project/"agent/second.json").write_text(json.dumps(actions),encoding="utf-8")
        (project/"second-settings.json").write_text(json.dumps(schema),encoding="utf-8")
        second=packages.install_project(key)
        rejects(lambda:agent.execute_action("apps.invoke",approved),agent.AppAgentError)
        assert len(runtime.list_events(key,topic="demo.reset"))==before
        releases.promote(key,first["release_id"])
        restored=apps.get(key)
        assert restored["installed_version"]==first["version"]
        assert restored["metadata"]["agent_actions"]=="agent/actions.json"
        assert restored["metadata"]["settings_schema"]=="settings.schema.json"
        assert control.manifest(key)["count"]==2
        assert control.settings(key)["values"]["limit"]==77
        rejects(lambda:releases.promote(key,"apprel_x/../../escape"),releases.AppReleaseError)

        # Post-activation failure restores the previous selected release and controls.
        before_app=apps.get(key)
        before_state=packages._read_state(key)
        with patch.object(resources,"enforce_sqlite_quota",side_effect=RuntimeError("injected activation failure")):
            rejects(lambda:packages.install_project(key),RuntimeError)
        assert packages._read_state(key)==before_state
        recovered=apps.get(key)
        assert recovered["installed_version"]==before_app["installed_version"]
        assert recovered["lifecycle_state"]==before_app["lifecycle_state"]
        assert recovered["metadata"]==before_app["metadata"]
        assert control.manifest(key)["count"]==2

        # Re-registration cannot silently undo an owner's pause or revocation.
        plugin={"plugin_key":"section16.plugin","name":"Section16 Plugin","version":"1.0","tools":[{
            "key":"lookup","mode":"read","handler_key":"lookup",
            "input_schema":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"],"additionalProperties":False},
        }]}
        plugins.register_plugin(plugin,trusted=True)
        calls=[]
        plugins.register_tool_handler(plugin["plugin_key"],"lookup",lambda args,context:calls.append(args) or {"answer":"ok"})
        tool=next(row for row in plugins.available_model_tools(owner=True) if row["plugin_key"]==plugin["plugin_key"])
        for arguments in ({},{"query":4},{"query":"ok","unexpected":True}):
            rejects(lambda:plugins.execute_model_tool(tool["model_name"],arguments,owner=True,source_app_key="homeserver-agent"),plugins.PluginError)
        assert calls==[]
        plugins.execute_model_tool(tool["model_name"],{"query":"ok"},owner=True,source_app_key="homeserver-agent")
        assert calls==[{"query":"ok"}]
        for state in ("paused","revoked"):
            plugins.set_plugin_status(plugin["plugin_key"],state)
            plugins.register_plugin({**plugin,"version":"2.0"},trusted=True)
            assert plugins.get_plugin(plugin["plugin_key"])["status"]==state
            rejects(lambda:plugins.execute_model_tool(tool["model_name"],{"query":"blocked"},owner=True,source_app_key="homeserver-agent"),plugins.PluginError)
        assert len(calls)==1
        plugins.set_plugin_status(plugin["plugin_key"],"active")
        plugins.execute_model_tool(tool["model_name"],{"query":"enabled"},owner=True,source_app_key="homeserver-agent")
        assert len(calls)==2

print("Section 16 app settings, dispatch, approval, release recovery and plugin lifecycle: PASS")

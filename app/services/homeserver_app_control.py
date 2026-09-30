from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import homeserver_app_packages, homeserver_app_runtime, homeserver_apps

CONTRACT="vp3.app.agent-control.v3"
MANIFEST_CONTRACTS={"vp3.app.agent-actions.v1","vp3.app.agent-actions.v2"}
RISK_LEVELS={"read","write","destructive","consequential","background","admin"}
_ACTION_KEY=re.compile(r"^[a-z][a-z0-9_.-]{2,119}$")


class AppControlError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe_rel(value:str)->Path:
    raw=str(value or "").replace("\\","/").strip()
    rel=Path(raw)
    if not raw or rel.is_absolute() or ".." in rel.parts:
        raise AppControlError("Agent action manifest path is invalid.")
    return rel


def validate_action_manifest(content_root:Path,app_key:str,manifest_path:str)->dict[str,Any]:
    root=content_root.resolve()
    target=(root/_safe_rel(manifest_path)).resolve()
    if root not in target.parents or not target.is_file():
        raise AppControlError("Agent action manifest is unavailable.")
    try:
        payload=json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppControlError("Agent action manifest is invalid JSON.") from exc
    if not isinstance(payload,dict) or payload.get("contract") not in MANIFEST_CONTRACTS:
        raise AppControlError("Agent action manifest contract is unsupported.")
    raw=payload.get("actions",[])
    if not isinstance(raw,list) or len(raw)>128:
        raise AppControlError("Agent action manifest actions are invalid.")
    actions=[]
    seen=set()
    for row in raw:
        if not isinstance(row,dict):
            raise AppControlError("Agent action definition must be an object.")
        key=str(row.get("key") or "").strip()
        if not _ACTION_KEY.fullmatch(key) or key in seen:
            raise AppControlError("Agent action key is invalid or duplicated.")
        seen.add(key)
        risk=str(row.get("risk") or "write").strip().lower()
        if risk not in RISK_LEVELS:
            raise AppControlError(f"Agent action {key} has an unsupported risk class.")
        confirmation=bool(row.get("requires_confirmation",risk in {"destructive","consequential","admin"}))
        schema=row.get("input_schema",{"type":"object","properties":{},"additionalProperties":False})
        if not isinstance(schema,dict) or schema.get("type")!="object":
            raise AppControlError(f"Agent action {key} input_schema must be an object schema.")
        executor=row.get("executor")
        if executor is not None:
            if not isinstance(executor,dict):
                raise AppControlError(f"Agent action {key} executor must be an object.")
            kind=str(executor.get("type") or "")
            if kind not in {"event.emit","job.run","builtin"}:
                raise AppControlError(f"Agent action {key} executor type is unsupported.")
            if kind=="event.emit":
                topic=str(executor.get("topic") or "").strip()
                if not topic:
                    raise AppControlError(f"Agent action {key} event topic is required.")
            elif kind=="job.run":
                job_id=str(executor.get("job_id") or "").strip()
                if not job_id:
                    raise AppControlError(f"Agent action {key} job_id is required.")
            elif kind=="builtin":
                provider=str(executor.get("provider") or "").strip()
                if not provider:
                    raise AppControlError(f"Agent action {key} builtin provider is required.")
        actions.append({
            "key":key,
            "name":str(row.get("name") or key)[:160],
            "description":str(row.get("description") or "")[:1000],
            "risk":risk,
            "requires_confirmation":confirmation,
            "input_schema":schema,
            "executor":executor,
        })
    return {
        "contract":CONTRACT,
        "manifest_contract":payload["contract"],
        "app_key":app_key,
        "actions":actions,
        "count":len(actions),
        "compatible":True,
    }


def manifest(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if not app.get("installed_version"):
        raise AppControlError("App is not installed.",409)
    metadata=dict(app.get("metadata") or {})
    manifest_path=str(metadata.get("agent_actions") or "agent/actions.json")
    try:
        root=homeserver_app_packages.active_content_root(app_key)
        return validate_action_manifest(root,app_key,manifest_path)
    except homeserver_app_packages.AppPackageError as exc:
        raise AppControlError(str(exc),exc.status_code) from exc


def action_spec(app_key:str,action_key:str)->dict[str,Any]:
    key=str(action_key or "").strip()
    for row in manifest(app_key)["actions"]:
        if row["key"]==key:
            return row
    raise AppControlError("App action not found.",404)


def _builtin(app_key:str,provider:str,action_key:str,arguments:dict[str,Any])->Any:
    if provider=="video_editor":
        from . import homeserver_video_editor
        if app_key!=homeserver_video_editor.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_video_editor.invoke(action_key,arguments)
    raise AppControlError("Builtin app action provider is unavailable.",501)


def invoke(app_key:str,action_key:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app.get("lifecycle_state")!="running":
        raise AppControlError("App must be running before invoking an app action.",409)
    spec=action_spec(app_key,action_key)
    executor=spec.get("executor")
    if not isinstance(executor,dict):
        raise AppControlError("App action has no executable handler.",409)
    args=dict(arguments or {})
    kind=str(executor.get("type") or "")
    if kind=="event.emit":
        base=dict(executor.get("payload") or {})
        base.update(args)
        result=homeserver_app_runtime.publish_event(
            app_key,str(executor.get("topic") or ""),base,source="agent"
        )
    elif kind=="job.run":
        result=homeserver_app_runtime.run_job(app_key,str(executor.get("job_id") or ""))
    elif kind=="builtin":
        result=_builtin(app_key,str(executor.get("provider") or ""),action_key,args)
    else:
        raise AppControlError("App action executor is unsupported.")
    return {
        "contract":"vp3.app.agent-action-result.v1",
        "app_key":app_key,
        "action":action_key,
        "risk":spec["risk"],
        "result":result,
    }


def compatibility(app_key:str)->dict[str,Any]:
    try:
        data=manifest(app_key)
        return {
            "contract":CONTRACT,
            "app_key":app_key,
            "compatible":True,
            "manifest_contract":data["manifest_contract"],
            "action_count":data["count"],
            "supported_manifest_contracts":sorted(MANIFEST_CONTRACTS),
        }
    except Exception as exc:
        return {
            "contract":CONTRACT,
            "app_key":app_key,
            "compatible":False,
            "reason":str(exc)[:500],
            "supported_manifest_contracts":sorted(MANIFEST_CONTRACTS),
        }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "manifest_contracts":sorted(MANIFEST_CONTRACTS),
        "risk_classes":sorted(RISK_LEVELS),
        "generic_action_discovery":True,
        "generic_invocation":True,
        "generic_executors":["event.emit","job.run","builtin"],
        "install_time_validation":True,
        "compatibility_negotiation":True,
        "owner_confirmation_for_high_risk":True,
        "homeserver_execution_authority":True,
    }

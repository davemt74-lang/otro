from __future__ import annotations

import json
from typing import Any, Callable

from ..database import db
from . import action_policy, agent_tools, app_scopes, homeserver_app_approvals, homeserver_app_prebuilt, homeserver_app_releases, homeserver_apps, tools

CONTRACT="vp3.app.agent-integration.v1"
READ_ACTIONS={"apps.list","apps.get","apps.releases"}
WRITE_ACTIONS={
    "apps.prebuilt.install",
    "apps.build_install",
    "apps.rollback",
    "apps.recover",
    "apps.start",
    "apps.stop",
}


class AppAgentError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe_app(item:dict[str,Any])->dict[str,Any]:
    metadata=dict(item.get("metadata") or {})
    allowed_meta={
        key:metadata[key]
        for key in (
            "runtime","entrypoint","sdk_version","prebuilt_app","vp3_managed",
            "prebuilt_category","active_release_id","previous_release_id",
            "package_sha256","sample_data_available","sample_data_item_count",
        )
        if key in metadata
    }
    return {
        "app_key":item["app_key"],
        "name":item["name"],
        "app_class":item["app_class"],
        "source_type":item["source_type"],
        "lifecycle_state":item["lifecycle_state"],
        "installed_version":item.get("installed_version"),
        "desired_version":item.get("desired_version"),
        "protected_system_app":bool(item.get("protected_system_app")),
        "metadata":allowed_meta,
        "updated_at":item.get("updated_at"),
    }


def list_apps(arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    unknown=set(args)-{"app_class","state","limit"}
    if unknown:
        raise AppAgentError(f"Unsupported apps.list argument: {sorted(unknown)[0]}")
    app_class=str(args.get("app_class") or "").strip().lower()
    if app_class and app_class not in {"system","user"}:
        raise AppAgentError("apps.list app_class must be system or user.")
    state=str(args.get("state") or "").strip().lower()
    if state and state not in homeserver_apps.LIFECYCLE_STATES:
        raise AppAgentError("apps.list state is invalid.")
    try:
        limit=int(args.get("limit",50))
    except (TypeError,ValueError) as exc:
        raise AppAgentError("apps.list limit must be an integer.") from exc
    limit=max(1,min(100,limit))
    payload=homeserver_apps.list_apps()
    rows=[]
    for item in payload["apps"]:
        if app_class and item["app_class"]!=app_class:
            continue
        if state and item["lifecycle_state"]!=state:
            continue
        rows.append(_safe_app(item))
        if len(rows)>=limit:
            break
    return {
        "contract":CONTRACT,
        "apps":rows,
        "count":len(rows),
        "counts":payload["counts"],
    }


def get_app(arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    if set(args)-{"app_key"}:
        raise AppAgentError("apps.get accepts only app_key.")
    key=str(args.get("app_key") or "").strip().lower()
    if not key:
        raise AppAgentError("apps.get requires app_key.")
    try:
        app=homeserver_apps.get(key)
    except homeserver_apps.HomeServerAppError as exc:
        raise AppAgentError(str(exc),exc.status_code) from exc
    safe=_safe_app(app)
    safe["history"]=[
        {
            "event_type":row["event_type"],
            "from_state":row.get("from_state"),
            "to_state":row.get("to_state"),
            "actor_type":row.get("actor_type"),
            "created_at":row.get("created_at"),
        }
        for row in homeserver_apps.history(key,20)
    ]
    try:
        releases=homeserver_app_releases.list_releases(key)
        safe["release_status"]={
            "active_release_id":releases.get("active_release_id"),
            "previous_release_id":releases.get("previous_release_id"),
            "count":releases.get("count",0),
        }
    except Exception:
        safe["release_status"]={"active_release_id":None,"previous_release_id":None,"count":0}
    return {"contract":CONTRACT,"app":safe}


def releases(arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    if set(args)-{"app_key","limit"}:
        raise AppAgentError("apps.releases accepts app_key and limit.")
    key=str(args.get("app_key") or "").strip().lower()
    if not key:
        raise AppAgentError("apps.releases requires app_key.")
    try:
        limit=int(args.get("limit",10))
    except (TypeError,ValueError) as exc:
        raise AppAgentError("apps.releases limit must be an integer.") from exc
    limit=max(1,min(20,limit))
    try:
        status=homeserver_app_releases.list_releases(key)
    except (homeserver_apps.HomeServerAppError,homeserver_app_releases.AppReleaseError) as exc:
        raise AppAgentError(str(exc),getattr(exc,"status_code",400)) from exc
    rows=[]
    for release in status["releases"][:limit]:
        rows.append({
            "release_id":release["release_id"],
            "version":release.get("version"),
            "runtime":release.get("runtime"),
            "package_sha256":release.get("package_sha256"),
            "active":bool(release.get("active")),
            "previous":bool(release.get("previous")),
        })
    return {
        "contract":CONTRACT,
        "app_key":key,
        "active_release_id":status.get("active_release_id"),
        "previous_release_id":status.get("previous_release_id"),
        "releases":rows,
        "count":len(rows),
    }


def normalize_action(action_key:str,arguments:dict[str,Any]|None)->dict[str,Any]:
    action=str(action_key or "").strip()
    if action not in WRITE_ACTIONS:
        raise AppAgentError("Unsupported Apps Agent action.")
    args=dict(arguments or {})
    unknown=set(args)-{"app_key"}
    if unknown:
        raise AppAgentError(f"Unsupported {action} argument: {sorted(unknown)[0]}")
    key=str(args.get("app_key") or "").strip().lower()
    if not key:
        raise AppAgentError(f"{action} requires app_key.")

    if action=="apps.prebuilt.install":
        catalog={item["key"]:item for item in homeserver_app_prebuilt.catalog()["packages"]}
        if key not in catalog:
            raise AppAgentError("VP3 prebuilt app not found.",404)
        return {"app_key":key}

    try:
        app=homeserver_apps.get(key)
    except homeserver_apps.HomeServerAppError as exc:
        raise AppAgentError(str(exc),exc.status_code) from exc

    if action=="apps.build_install":
        if app["app_class"]!="user" or app["source_type"] not in {"user_created","agent_builder"}:
            raise AppAgentError("Build & install is only available to local SDK user apps.",409)
    elif action in {"apps.rollback","apps.recover"}:
        if app["app_class"]!="user":
            raise AppAgentError("VP3 system app release recovery is managed by VP3.",409)
    elif action in {"apps.start","apps.stop"}:
        if app["app_class"]!="user":
            raise AppAgentError("VP3 system app lifecycle is managed by VP3.",409)
    return {"app_key":key}


def safe_action_meta(action_key:str,arguments:dict[str,Any]|None)->dict[str,Any]:
    args=dict(arguments or {})
    return {
        "action":str(action_key or "")[:80],
        "app_key":str(args.get("app_key") or "")[:80],
        "argument_count":len(args),
        "owner_confirmation_required":True,
    }


def execute_action(action_key:str,arguments:dict[str,Any])->dict[str,Any]:
    args=normalize_action(action_key,arguments)
    key=args["app_key"]
    try:
        if action_key=="apps.prebuilt.install":
            return homeserver_app_prebuilt.install(key)
        if action_key=="apps.build_install":
            from . import homeserver_app_packages
            return {"release":homeserver_app_packages.install_project(key)}
        if action_key=="apps.rollback":
            return homeserver_app_releases.rollback(key)
        if action_key=="apps.recover":
            return homeserver_app_releases.recover(key)
        if action_key=="apps.start":
            return {"app":homeserver_apps.resume_user_app(key)}
        if action_key=="apps.stop":
            app=homeserver_apps.get(key)
            if app["lifecycle_state"]=="stopped":
                return {"app":app,"changed":False}
            return {"app":homeserver_apps.transition(
                key,"stopped",actor_type="owner",actor_key="agent-approved",
                metadata={"reason":"approved_agent_action"},
            ),"changed":True}
    except (homeserver_apps.HomeServerAppError,homeserver_app_releases.AppReleaseError) as exc:
        raise AppAgentError(str(exc),getattr(exc,"status_code",400)) from exc
    raise AppAgentError("Unsupported Apps Agent action.")


def record_context_read(action_key:str,result:dict[str,Any])->None:
    with db() as connection:
        connection.execute(
            """INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
               VALUES ('agent','homeserver-agent',?,'homeserver_apps','agent_context',?)""",
            (
                f"agent.{action_key}",
                json.dumps({"contract":CONTRACT,"result_count":int(result.get("count",1))},separators=(",",":")),
            ),
        )


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "agent_brain_context":True,
        "agent_chat_tools":True,
        "read_actions":sorted(READ_ACTIONS),
        "write_actions":sorted(WRITE_ACTIONS),
        "write_actions_require_owner_approval":True,
        "paired_app_admin_tools":False,
        "system_app_protection":True,
    }


MODEL_READ_TOOLS={
    "homeserver_apps_list":"apps.list",
    "homeserver_app_status":"apps.status",
    "homeserver_prebuilt_apps_list":"apps.prebuilt.list",
}
MODEL_WRITE_PROPOSALS={
    "homeserver_prebuilt_app_install_request":"apps.prebuilt.install",
    "homeserver_app_build_install_request":"apps.build_install",
    "homeserver_app_rollback_request":"apps.rollback",
    "homeserver_app_recover_request":"apps.recover",
}
MODEL_WRITE_DESCRIPTIONS={
    "homeserver_prebuilt_app_install_request":"Prepare installation or update of one trusted VP3 prebuilt app. This always creates a pending owner approval request and never installs immediately.",
    "homeserver_app_build_install_request":"Prepare build and installation of one user-created VP3 SDK app. This always creates a pending owner approval request and never changes the running app immediately.",
    "homeserver_app_rollback_request":"Prepare rollback of one user-created app to its previous release. This always creates a pending owner approval request.",
    "homeserver_app_recover_request":"Prepare recovery of one failed or degraded user-created app from canonical release history. This always creates a pending owner approval request.",
}


def install()->None:
    if getattr(agent_tools,"_homeserver_apps_v170_installed",False):
        return

    for model_name,tool_key in MODEL_READ_TOOLS.items():
        existing=agent_tools.MODEL_TOOL_NAMES.get(model_name)
        if existing not in (None,tool_key):
            raise RuntimeError(f"Agent tool name collision: {model_name}")
        agent_tools.MODEL_TOOL_NAMES[model_name]=tool_key

    original_schemas:Callable[...,list[dict[str,Any]]]=agent_tools.model_tool_schemas

    def model_tool_schemas(
        granted_permissions:set[str]|None=None,
        *,
        owner:bool=False,
        allow_write_proposals:bool=False,
        source_app_key:str|None=None,
    )->list[dict[str,Any]]:
        schemas=original_schemas(
            granted_permissions,
            owner=owner,
            allow_write_proposals=allow_write_proposals,
            source_app_key=source_app_key,
        )
        if not allow_write_proposals:
            return schemas
        by_key={item["key"]:item for item in tools.list_tools(granted_permissions,owner=owner)}
        scope=None
        if not owner and source_app_key:
            scope=app_scopes.get_scope_for_source(source_app_key)
        for model_name,tool_key in MODEL_WRITE_PROPOSALS.items():
            item=by_key.get(tool_key)
            if not item or item.get("mode")!="write" or not item.get("available"):
                continue
            if scope is not None and not app_scopes.tool_allowed(scope,tool_key):
                continue
            execution=agent_tools._execution_policy(source_app_key,tool_key,owner)
            if execution and execution["policy_mode"]==action_policy.SENSITIVE_HIGH_IMPACT:
                continue
            schemas.append({
                "type":"function",
                "function":{
                    "name":model_name,
                    "description":MODEL_WRITE_DESCRIPTIONS[model_name],
                    "parameters":item["input_schema"],
                },
            })
        return schemas

    agent_tools.model_tool_schemas=model_tool_schemas
    original_execute=agent_tools.execute_model_tool

    def execute_model_tool(
        source_app_key:str,
        model_tool_name:str,
        arguments:dict[str,Any]|None,
        granted_permissions:set[str]|None=None,
        *,
        owner:bool=False,
    )->dict[str,Any]:
        tool_key=MODEL_WRITE_PROPOSALS.get(model_tool_name)
        if tool_key is None:
            return original_execute(
                source_app_key,
                model_tool_name,
                arguments,
                granted_permissions,
                owner=owner,
            )

        global_policy=agent_tools.get_policy()
        if not global_policy["enabled"] or not global_policy["allow_write_proposals"]:
            raise agent_tools._deny_unavailable(source_app_key,owner)

        granted=set(granted_permissions or set())
        available={
            item["key"]:item for item in tools.list_tools(granted,owner=owner)
            if item.get("available")
        }
        write_tool=available.get(tool_key)
        if not write_tool or write_tool.get("mode")!="write":
            raise agent_tools._deny_unavailable(source_app_key,owner)

        execution=agent_tools._execution_policy(source_app_key,tool_key,owner)
        if execution and execution["policy_mode"]==action_policy.SENSITIVE_HIGH_IMPACT:
            agent_tools._record_policy(
                execution,"blocked",
                reason="HomeServer Apps consequential actions require local owner approval.",
            )
            raise agent_tools._deny_unavailable(source_app_key,owner)
        args=arguments or {}
        if not agent_tools._scope_allows(source_app_key,tool_key,args,execution):
            agent_tools._record_policy(
                execution,"blocked",
                reason="The current application scope blocks this HomeServer Apps action.",
            )
            raise agent_tools._deny_unavailable(source_app_key,owner)

        # Deliberately proposal-only: no safe-automatic branch exists here.
        # Installation and release mutations always require an explicit owner
        # approval, even if a future policy row is accidentally permissive.
        try:
            result=homeserver_app_approvals.create_request(
                source_app_key,tool_key,args,owner=owner
            )
        except Exception as exc:
            raise agent_tools.AgentToolError(str(exc)) from exc
        request_id=str(((result.get("result") or {}).get("request_id") or "")) or None
        agent_tools._record_policy(
            execution,"approval_requested",request_id=request_id,
            reason="Agent Apps action requires explicit local owner approval before execution.",
        )
        return result

    agent_tools.execute_model_tool=execute_model_tool
    agent_tools._homeserver_apps_v170_installed=True

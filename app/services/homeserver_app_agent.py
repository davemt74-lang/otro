from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from ..database import db
from . import homeserver_app_prebuilt, homeserver_app_releases, homeserver_app_sources, homeserver_app_workspace, homeserver_apps

CONTRACT="vp3.app.agent-integration.v1"
READ_ACTIONS={"apps.list","apps.get","apps.releases","apps.source.status","apps.workspace.status","apps.workspace.file.read"}
WRITE_ACTIONS={
    "apps.prebuilt.install",
    "apps.build_install",
    "apps.rollback",
    "apps.recover",
    "apps.start",
    "apps.stop",
    "apps.git.inspect",
    "apps.source.install",
    "apps.source.detach",
    "apps.workspace.file.write",
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
    try:
        source=homeserver_app_sources.source_status(key)
        current=source.get("current")
        safe["source_status"]={
            "source_type":source.get("source_type"),
            "source_ref":source.get("source_ref"),
            "update_available":bool(source.get("update_available")),
            "source_revision":current.get("source_revision") if current else None,
            "package_sha256":current.get("package_sha256") if current else None,
        }
    except Exception:
        safe["source_status"]={"source_type":safe.get("source_type"),"source_ref":"","update_available":False}
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


def source_status(arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    if set(args)-{"app_key"}:
        raise AppAgentError("apps.source.status accepts only app_key.")
    key=str(args.get("app_key") or "").strip().lower()
    if not key:
        raise AppAgentError("apps.source.status requires app_key.")
    try:
        status=homeserver_app_sources.source_status(key)
    except (homeserver_apps.HomeServerAppError,homeserver_app_sources.AppSourceError) as exc:
        raise AppAgentError(str(exc),getattr(exc,"status_code",400)) from exc
    current=status.get("current")
    return {
        "contract":CONTRACT,
        "app_key":key,
        "source_type":status.get("source_type"),
        "source_ref":status.get("source_ref"),
        "installed_package_sha256":status.get("installed_package_sha256"),
        "update_available":bool(status.get("update_available")),
        "current":{
            "source_id":current.get("source_id"),
            "source_type":current.get("source_type"),
            "source_ref":current.get("source_ref"),
            "source_revision":current.get("source_revision"),
            "package_sha256":current.get("package_sha256"),
            "status":current.get("status"),
            "version":(current.get("manifest") or {}).get("version"),
        } if current else None,
    }


def workspace_status(arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    if set(args)-{"app_key"}:
        raise AppAgentError("apps.workspace.status accepts only app_key.")
    key=str(args.get("app_key") or "").strip().lower()
    if not key:
        raise AppAgentError("apps.workspace.status requires app_key.")
    try:
        state=homeserver_app_workspace.status(key)
    except homeserver_app_workspace.AppWorkspaceError as exc:
        raise AppAgentError(str(exc),exc.status_code) from exc
    return {
        "contract":CONTRACT,
        "app_key":key,
        "validation":state.get("validation"),
        "validation_error":state.get("validation_error"),
        "project":{"count":state.get("project",{}).get("count",0),"files":state.get("project",{}).get("files",[])},
        "permissions":state.get("permissions"),
        "runtime":state.get("runtime"),
        "releases":state.get("releases"),
        "source":state.get("source"),
    }


def workspace_file_read(arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    if set(args)-{"app_key","path"}:
        raise AppAgentError("apps.workspace.file.read accepts app_key and path.")
    key=str(args.get("app_key") or "").strip().lower()
    path=str(args.get("path") or "").strip()
    if not key or not path:
        raise AppAgentError("apps.workspace.file.read requires app_key and path.")
    try:
        return homeserver_app_workspace.read_file(key,path)
    except homeserver_app_workspace.AppWorkspaceError as exc:
        raise AppAgentError(str(exc),exc.status_code) from exc


def normalize_action(action_key:str,arguments:dict[str,Any]|None)->dict[str,Any]:
    action=str(action_key or "").strip()
    if action not in WRITE_ACTIONS:
        raise AppAgentError("Unsupported Apps Agent action.")
    args=dict(arguments or {})

    if action=="apps.git.inspect":
        unknown=set(args)-{"repo_url","ref"}
        if unknown:
            raise AppAgentError(f"Unsupported {action} argument: {sorted(unknown)[0]}")
        try:
            repo_url=homeserver_app_sources._safe_public_url(str(args.get("repo_url") or ""))
            ref=homeserver_app_sources._git_ref(str(args.get("ref") or "HEAD"))
        except homeserver_app_sources.AppSourceError as exc:
            raise AppAgentError(str(exc),exc.status_code) from exc
        return {"repo_url":repo_url,"ref":ref}

    if action=="apps.workspace.file.write":
        unknown=set(args)-{"app_key","path","content"}
        if unknown:
            raise AppAgentError(f"Unsupported {action} argument: {sorted(unknown)[0]}")
        key=str(args.get("app_key") or "").strip().lower()
        path=str(args.get("path") or "").strip()
        content=str(args.get("content") or "")
        if not key or not path:
            raise AppAgentError("apps.workspace.file.write requires app_key and path.")
        try:
            homeserver_app_workspace._app(key)
            homeserver_app_workspace._rel(path,allow_missing=True)
        except homeserver_app_workspace.AppWorkspaceError as exc:
            raise AppAgentError(str(exc),exc.status_code) from exc
        if len(content.encode("utf-8"))>homeserver_app_workspace.MAX_TEXT_BYTES:
            raise AppAgentError("Workspace file exceeds the editor size limit.",413)
        return {"app_key":key,"path":path,"content":content}

    if action=="apps.source.install":
        unknown=set(args)-{"source_id"}
        if unknown:
            raise AppAgentError(f"Unsupported {action} argument: {sorted(unknown)[0]}")
        source_id=str(args.get("source_id") or "").strip()
        if not source_id or len(source_id)>80:
            raise AppAgentError("apps.source.install requires source_id.")
        try:
            source=homeserver_app_sources.get_source(source_id)
        except homeserver_app_sources.AppSourceError as exc:
            raise AppAgentError(str(exc),exc.status_code) from exc
        return {"source_id":source["source_id"]}

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
    elif action=="apps.source.detach":
        if app["app_class"]!="user":
            raise AppAgentError("VP3 system app sources are managed by VP3.",409)
    return {"app_key":key}


def safe_action_meta(action_key:str,arguments:dict[str,Any]|None)->dict[str,Any]:
    args=dict(arguments or {})
    return {
        "action":str(action_key or "")[:80],
        "app_key":str(args.get("app_key") or "")[:80],
        "source_id":str(args.get("source_id") or "")[:80],
        "repo_host":urlsplit(str(args.get("repo_url") or "")).hostname or "",
        "path":str(args.get("path") or "")[:240],
        "content_bytes":len(str(args.get("content") or "").encode("utf-8")) if "content" in args else 0,
        "argument_count":len(args),
        "owner_confirmation_required":True,
    }


def execute_action(action_key:str,arguments:dict[str,Any])->dict[str,Any]:
    args=normalize_action(action_key,arguments)
    try:
        if action_key=="apps.git.inspect":
            return {"source":homeserver_app_sources.inspect_git(args["repo_url"],args["ref"])}
        if action_key=="apps.source.install":
            return homeserver_app_sources.install_source(args["source_id"],approved=True)
        if action_key=="apps.workspace.file.write":
            return {"workspace":homeserver_app_workspace.write_file(args["app_key"],args["path"],args["content"])}
        key=args["app_key"]
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
        if action_key=="apps.source.detach":
            return {"source":homeserver_app_sources.detach(key,confirmed=True)}
    except (homeserver_apps.HomeServerAppError,homeserver_app_releases.AppReleaseError,homeserver_app_sources.AppSourceError) as exc:
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

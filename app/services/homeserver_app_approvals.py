from __future__ import annotations

from typing import Any

from . import approvals, homeserver_app_packages, homeserver_app_prebuilt, homeserver_app_releases, homeserver_app_sdk, homeserver_apps


APP_ACTIONS={
    "apps.prebuilt.install",
    "apps.build_install",
    "apps.rollback",
    "apps.recover",
}


def _app_key(arguments:dict[str,Any]|None)->str:
    payload=dict(arguments or {})
    unknown=set(payload)-{"app_key"}
    if unknown:
        raise approvals.ApprovalError(f"Unsupported Apps proposal argument: {sorted(unknown)[0]}")
    key=str(payload.get("app_key") or "").strip().lower()
    if not homeserver_apps._KEY_RE.fullmatch(key):
        raise approvals.ApprovalError("A valid app_key is required.")
    return key


def _safe_meta(action_key:str,app_key:str)->dict[str,Any]:
    return {"action":action_key,"app_key":app_key,"argument_count":1}


def create_request(source_app_key:str,action_key:str,arguments:dict[str,Any]|None,*,owner:bool=False)->dict[str,Any]:
    if action_key not in APP_ACTIONS:
        raise approvals.ApprovalError("Unsupported HomeServer Apps action.")
    source=source_app_key.strip() or ("owner" if owner else "app:unknown")
    actor_type="owner" if owner else "app"
    key=_app_key(arguments)

    try:
        if action_key=="apps.prebuilt.install":
            catalog={item["key"]:item for item in homeserver_app_prebuilt.catalog().get("packages",[])}
            if key not in catalog:
                raise approvals.ApprovalError("VP3 prebuilt app not found.",404)
        elif action_key=="apps.build_install":
            app=homeserver_apps.get(key)
            if app["app_class"]!="user":
                raise approvals.ApprovalError("Only user apps can be built from an SDK project.",409)
            if not homeserver_app_sdk.app_root(key).is_dir():
                raise approvals.ApprovalError("User app project is missing.",404)
        elif action_key=="apps.rollback":
            app=homeserver_apps.get(key)
            if app["app_class"]!="user":
                raise approvals.ApprovalError("VP3 system app rollback is managed by VP3.",409)
            state=homeserver_app_packages._read_state(key)
            if not state.get("previous_release_id"):
                raise approvals.ApprovalError("No previous app release is available.",409)
        elif action_key=="apps.recover":
            app=homeserver_apps.get(key)
            if app["app_class"]!="user":
                raise approvals.ApprovalError("VP3 system app recovery is managed by VP3.",409)
            if app["lifecycle_state"] not in {"failed","degraded"}:
                raise approvals.ApprovalError("Recovery is only available for failed or degraded apps.",409)
            state=homeserver_app_packages._read_state(key)
            if not (state.get("previous_release_id") or state.get("active_release_id")):
                raise approvals.ApprovalError("No recovery release is available.",409)
    except homeserver_apps.HomeServerAppError as exc:
        raise approvals.ApprovalError(str(exc),exc.status_code) from exc
    except homeserver_app_releases.AppReleaseError as exc:
        raise approvals.ApprovalError(str(exc),exc.status_code) from exc

    required=[] if owner else ["apps.manage","tools.execute"]
    return approvals._create_action_request(
        source,
        actor_type,
        action_key,
        {"app_key":key},
        _safe_meta(action_key,key),
        required,
    )

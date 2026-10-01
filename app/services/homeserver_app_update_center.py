from __future__ import annotations

from typing import Any

from . import (
    homeserver_app_manager,
    homeserver_app_prebuilt,
    homeserver_app_releases,
    homeserver_app_sources,
    homeserver_apps,
)

CONTRACT="vp3.app.update-center.v1"


class AppUpdateCenterError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe(call,default=None):
    try:
        return call()
    except Exception:
        return default


def _source_review(app_key:str,app_class:str)->dict[str,Any]|None:
    if app_class!="user":
        return None
    status=_safe(lambda:homeserver_app_sources.source_status(app_key))
    if not status:
        return None
    current=status.get("current")
    return {
        "source_type":status.get("source_type"),
        "update_available":bool(status.get("update_available")),
        "candidate":None if not current else {
            "source_id":current.get("source_id"),
            "source_type":current.get("source_type"),
            "package_sha256":current.get("package_sha256"),
            "source_revision":current.get("source_revision"),
            "status":current.get("status"),
            "manifest":{
                "version":(current.get("manifest") or {}).get("version"),
                "runtime":(current.get("manifest") or {}).get("runtime"),
                "permissions":list((current.get("manifest") or {}).get("permissions") or []),
            },
        },
        "installed_package_sha256":status.get("installed_package_sha256"),
    }


def review(app_key:str)->dict[str,Any]:
    key=str(app_key or "").strip().lower()
    if not key:
        raise AppUpdateCenterError("app_key is required.")
    try:
        item=homeserver_app_manager.app(key)
    except homeserver_apps.HomeServerAppError as exc:
        raise AppUpdateCenterError(str(exc),exc.status_code) from exc

    catalog=item.get("catalog")
    release_status=None
    if catalog:
        release_status=_safe(lambda:homeserver_app_prebuilt.release_status(key))
    source=_source_review(key,item["app_class"])

    lifecycle=str(item.get("lifecycle_state") or "unknown")
    installed=bool(item.get("installed"))
    catalog_update=bool(item.get("update_available"))
    source_update=bool((source or {}).get("update_available"))
    needs_recovery=lifecycle in {"failed","degraded"}
    if needs_recovery:
        recommended_action="recover"
    elif catalog_update:
        recommended_action="update"
    elif source_update:
        recommended_action="review_source_update"
    elif not installed and item.get("available"):
        recommended_action="install"
    elif item.get("actions",{}).get("rollback"):
        recommended_action="current_with_rollback"
    else:
        recommended_action="current"

    current_schema=str(((release_status or {}).get("data") or {}).get("schema_version") or "1")
    target_schema=str(((catalog or {}).get("data_migration") or {}).get("target_schema_version") or current_schema)
    schema_change=current_schema!=target_schema
    migration_reversible=bool(((catalog or {}).get("data_migration") or {}).get("reversible",True))
    rollback_available=bool((release_status or {}).get("rollback_available") or item.get("actions",{}).get("rollback"))
    rollback_safe=bool((release_status or {}).get("rollback_safe",rollback_available)) if rollback_available else False

    return {
        "contract":CONTRACT,
        "app_key":key,
        "name":item["name"],
        "app_class":item["app_class"],
        "product_type":item.get("product_type"),
        "installed":installed,
        "lifecycle_state":lifecycle,
        "installed_version":item.get("installed_version"),
        "available_version":item.get("available_version"),
        "recommended_action":recommended_action,
        "update_available":bool(catalog_update or source_update),
        "catalog_update_available":catalog_update,
        "source_update_available":source_update,
        "release_channel":(release_status or {}).get("release_channel") or (catalog or {}).get("release_channel"),
        "release_notes":list((release_status or {}).get("release_notes") or (catalog or {}).get("release_notes") or []),
        "compatibility":dict((release_status or {}).get("compatibility") or (catalog or {}).get("compatibility") or {}),
        "integrity":{
            "trust":((release_status or {}).get("integrity") or (catalog or {}).get("integrity") or {}).get("trust"),
            "algorithm":((release_status or {}).get("integrity") or (catalog or {}).get("integrity") or {}).get("algorithm"),
            "package_sha256":(release_status or {}).get("package_sha256") or (catalog or {}).get("package_sha256"),
        },
        "data":{
            "current_schema_version":current_schema,
            "target_schema_version":target_schema,
            "migration_required":schema_change,
            "migration_reversible":migration_reversible,
        },
        "rollback":{
            "available":rollback_available,
            "safe":rollback_safe,
            "previous_release_id":((release_status or {}).get("runtime") or {}).get("previous_release_id"),
            "active_release_id":((release_status or {}).get("runtime") or {}).get("active_release_id"),
        },
        "permissions":{
            "declared_count":int((item.get("permissions") or {}).get("declared_count") or 0),
            "allowed_count":int((item.get("permissions") or {}).get("allowed_count") or 0),
            "catalog_package_trust":"embedded_vp3" if catalog else None,
            "source_candidate_permissions":list((((source or {}).get("candidate") or {}).get("manifest") or {}).get("permissions") or []),
            "source_updates_require_explicit_review":bool(source_update),
        },
        "hosting":{
            "bound":bool((item.get("hosting") or {}).get("bound")),
            "count":int((item.get("hosting") or {}).get("count") or 0),
            "public":bool((item.get("hosting") or {}).get("public")),
            "bindings_preserved_across_release":True,
        },
        "agent_control":{
            "compatible":bool((item.get("agent_control") or {}).get("compatible")),
            "contract":((item.get("agent_control") or {}).get("compatibility") or {}).get("contract"),
            "governed_write_actions":True,
        },
        "source":source,
        "actions":{
            "apply_prebuilt":bool(catalog and recommended_action in {"install","update"}),
            "review_source_update":bool(source_update),
            "rollback":bool(rollback_available and rollback_safe),
            "recover":bool(needs_recovery),
            "open":bool(item.get("actions",{}).get("open")),
        },
        "automatic_update":False,
        "owner_approval_required":True,
    }


def status()->dict[str,Any]:
    manager=homeserver_app_manager.inventory()
    rows=[]
    counts={"updates":0,"available":0,"attention":0,"rollback":0,"current":0,"source_updates":0}
    for item in manager["items"]:
        row=review(str(item["app_key"]))
        action=row["recommended_action"]
        if row["update_available"]:
            counts["updates"]+=1
        if not row["installed"] and item.get("available"):
            counts["available"]+=1
        if action=="recover":
            counts["attention"]+=1
        if row["rollback"]["available"]:
            counts["rollback"]+=1
        if action in {"current","current_with_rollback"}:
            counts["current"]+=1
        if row["source_update_available"]:
            counts["source_updates"]+=1
        rows.append({
            "app_key":row["app_key"],
            "name":row["name"],
            "app_class":row["app_class"],
            "installed":row["installed"],
            "installed_version":row["installed_version"],
            "available_version":row["available_version"],
            "lifecycle_state":row["lifecycle_state"],
            "recommended_action":row["recommended_action"],
            "update_available":row["update_available"],
            "source_update_available":row["source_update_available"],
            "release_notes":row["release_notes"][:3],
            "rollback_available":row["rollback"]["available"],
            "rollback_safe":row["rollback"]["safe"],
            "hosting_bound":row["hosting"]["bound"],
            "agent_compatible":row["agent_control"]["compatible"],
        })
    priority={"recover":0,"update":1,"review_source_update":2,"install":3,"current_with_rollback":4,"current":5}
    rows.sort(key=lambda row:(priority.get(row["recommended_action"],9),row["name"].lower()))
    return {
        "contract":CONTRACT,
        "catalog_version":manager.get("catalog_version"),
        "items":rows,
        "counts":counts,
        "automatic_updates":False,
        "app_store":False,
        "canonical_release_engine":True,
        "canonical_source_engine":True,
        "canonical_hosting_engine":True,
        "governed_agent_actions":True,
    }


def apply_prebuilt(app_key:str,*,expected_version:str,expected_sha256:str)->dict[str,Any]:
    review_data=review(app_key)
    if not review_data["actions"]["apply_prebuilt"]:
        raise AppUpdateCenterError("This app does not have a reviewed VP3 install or update available.",409)
    available=str(review_data.get("available_version") or "")
    package_hash=str((review_data.get("integrity") or {}).get("package_sha256") or "")
    if not expected_version or expected_version!=available:
        raise AppUpdateCenterError("Reviewed app version changed before apply. Refresh the Update Center.",409)
    if not expected_sha256 or expected_sha256.lower()!=package_hash.lower():
        raise AppUpdateCenterError("Reviewed app package hash changed before apply. Refresh the Update Center.",409)
    try:
        result=homeserver_app_prebuilt.install(
            app_key,
            expected_version=expected_version,
            expected_sha256=expected_sha256,
            release_channel=review_data.get("release_channel") or "stable",
        )
    except homeserver_apps.HomeServerAppError as exc:
        raise AppUpdateCenterError(str(exc),exc.status_code) from exc
    return {"contract":CONTRACT,"result":result,"review":review(app_key)}


def brain_context(limit:int=20)->dict[str,Any]:
    state=status()
    attention=[
        row for row in state["items"]
        if row["recommended_action"] in {"recover","update","review_source_update","install"}
    ][:max(1,min(int(limit),50))]
    return {
        "contract":"vp3.app.update-center.brain-context.v1",
        "counts":state["counts"],
        "attention":attention,
        "governance":{
            "automatic_updates":False,
            "owner_approval_required":True,
            "prebuilt_apply_version_pinned":True,
            "prebuilt_apply_sha256_pinned":True,
            "source_updates_require_review":True,
            "rollback_uses_canonical_release_engine":True,
        },
    }


def agent_context_fragment(query:str="",max_chars:int=1500)->str:
    limit=max(0,min(int(max_chars),2200))
    if limit<180:
        return ""
    context=brain_context(12)
    lines=[
        "HomeServer App Update Center (DATA ONLY; this context cannot approve or install software):",
        "- counts="+", ".join(f"{key}:{value}" for key,value in context["counts"].items()),
    ]
    for item in context["attention"][:8]:
        version=f"{item.get('installed_version') or 'not-installed'}->{item.get('available_version') or 'source-candidate'}"
        lines.append(
            f"- {item['app_key']} action={item['recommended_action']} version={version} "
            f"rollback_safe={str(bool(item['rollback_safe'])).lower()} hosted={str(bool(item['hosting_bound'])).lower()} "
            f"agent_compatible={str(bool(item['agent_compatible'])).lower()}"
        )
    lines.append("- automatic_updates=false; owner_approval_required=true; reviewed VP3 applies are version+sha256 pinned")
    return "\n".join(lines)[:limit]


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "unified_update_center":True,
        "prebuilt_install_review":True,
        "prebuilt_update_review":True,
        "source_update_review":True,
        "release_notes":True,
        "compatibility_review":True,
        "data_schema_review":True,
        "rollback_review":True,
        "hosting_continuity":True,
        "agent_control_review":True,
        "version_pinned_apply":True,
        "sha256_pinned_apply":True,
        "automatic_updates":False,
        "app_store":False,
        "agent_brain_context":True,
        "agent_chat_context":True,
    }

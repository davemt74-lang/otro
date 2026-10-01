from __future__ import annotations

import re
from typing import Any

from ..config import settings
from . import homeserver_app_manager

CONTRACT="vp3.homeserver.app-center.v1"


class AppCenterError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _text(value:Any)->str:
    return str(value or "").strip()


def _version_tuple(value:Any)->tuple[int,...]:
    parts=[int(x) for x in re.findall(r"\d+",_text(value))[:4]]
    return tuple(parts or [0])


def _readiness(item:dict[str,Any])->dict[str,Any]:
    catalog=dict(item.get("catalog") or {})
    installed=bool(item.get("installed"))
    update=bool(item.get("update_available"))
    lifecycle=_text(item.get("lifecycle_state"))
    migration=dict(catalog.get("data_migration") or {})
    integrity=dict(catalog.get("integrity") or {})
    compatibility=dict(catalog.get("compatibility") or {})
    issues:list[dict[str,str]]=[]
    warnings:list[dict[str,str]]=[]

    if item.get("available") and not catalog:
        issues.append({"code":"catalog_missing","message":"Catalog metadata is unavailable."})
    if catalog and not _text(integrity.get("package_sha256")):
        issues.append({"code":"integrity_missing","message":"Package SHA-256 metadata is unavailable."})
    minimum=_text(compatibility.get("min_homeserver_version"))
    maximum=_text(compatibility.get("max_homeserver_version"))
    current=_version_tuple(settings.version)
    if minimum and current<_version_tuple(minimum):
        issues.append({
            "code":"homeserver_version_too_old",
            "message":f"Requires HomeServer {minimum} or newer.",
        })
    if maximum and current>_version_tuple(maximum):
        issues.append({
            "code":"homeserver_version_too_new",
            "message":f"Supports HomeServer through {maximum}.",
        })
    if installed and lifecycle in {"failed","degraded"}:
        issues.append({"code":"runtime_attention","message":f"Installed app is {lifecycle}."})
    if update and not bool(migration.get("reversible",True)):
        warnings.append({
            "code":"irreversible_data_migration",
            "message":"This update declares an irreversible app-data migration; rollback may be unavailable.",
        })
    if installed:
        compatibility=dict((item.get("agent_control") or {}).get("compatibility") or {})
        if compatibility and not compatibility.get("compatible"):
            warnings.append({
                "code":"agent_control_incompatible",
                "message":"Installed app is not fully compatible with the current universal Agent control contract.",
            })

    operation="update" if update else ("install" if item.get("actions",{}).get("install") else "none")
    ready=not issues and operation in {"install","update"}
    return {
        "operation":operation,
        "ready":ready,
        "blocked":bool(issues),
        "issues":issues,
        "warnings":warnings,
        "requires_owner_action":operation in {"install","update"},
        "package_sha256":_text(integrity.get("package_sha256") or catalog.get("package_sha256")),
        "release_channel":_text(catalog.get("release_channel") or "stable"),
        "data_migration":{
            "target_schema_version":_text(migration.get("target_schema_version") or "1"),
            "reversible":bool(migration.get("reversible",True)),
        },
    }


def _project(item:dict[str,Any])->dict[str,Any]:
    catalog=dict(item.get("catalog") or {})
    readiness=_readiness(item)
    notes=list(catalog.get("release_notes") or [])
    attention=[]
    if item.get("update_available"):
        attention.append("update_available")
    if readiness["issues"]:
        attention.append("blocked")
    if readiness["warnings"]:
        attention.append("warning")
    if item.get("lifecycle_state") in {"failed","degraded"}:
        attention.append("runtime_attention")
    return {
        "app_key":item["app_key"],
        "name":item["name"],
        "description":item.get("description") or "",
        "category":item.get("category") or "App",
        "app_class":item.get("app_class"),
        "product_type":item.get("product_type"),
        "installed":bool(item.get("installed")),
        "available":bool(item.get("available")),
        "lifecycle_state":item.get("lifecycle_state"),
        "installed_version":item.get("installed_version"),
        "available_version":item.get("available_version"),
        "update_available":bool(item.get("update_available")),
        "release_notes":notes,
        "deployment_modes":list(item.get("deployment_modes") or []),
        "hosting":item.get("hosting"),
        "agent_control":item.get("agent_control"),
        "actions":item.get("actions"),
        "readiness":readiness,
        "attention":attention,
        "search_text":" ".join([
            _text(item.get("name")),_text(item.get("app_key")),_text(item.get("description")),
            _text(item.get("category"))," ".join(notes),
        ]).lower(),
    }


def catalog(
    q:str="",
    category:str="",
    view:str="all",
)->dict[str,Any]:
    inventory=homeserver_app_manager.inventory()
    query=_text(q).lower()
    category_key=_text(category).lower()
    view_key=_text(view).lower() or "all"
    allowed_views={"all","discover","installed","updates","attention","vp3","user","shared","hosted"}
    if view_key not in allowed_views:
        raise AppCenterError("Unsupported App Center view.")
    projected=[_project(item) for item in inventory.get("items",[])]

    def keep(item:dict[str,Any])->bool:
        if query and query not in item["search_text"]:
            return False
        if category_key and _text(item.get("category")).lower()!=category_key:
            return False
        if view_key=="discover" and (item["installed"] or not item["available"]): return False
        if view_key=="installed" and not item["installed"]: return False
        if view_key=="updates" and not item["update_available"]: return False
        if view_key=="attention" and not item["attention"]: return False
        if view_key=="vp3" and item["product_type"]!="vp3_optional_app": return False
        if view_key=="user" and item["app_class"]!="user": return False
        if view_key=="shared" and item["product_type"]!="private_shared_app": return False
        if view_key=="hosted" and not bool((item.get("hosting") or {}).get("bound")): return False
        return True

    items=[item for item in projected if keep(item)]
    items.sort(key=lambda row:(
        0 if row["update_available"] else 1,
        0 if row["attention"] else 1,
        _text(row["category"]).lower(),
        _text(row["name"]).lower(),
    ))
    categories=sorted({
        _text(item.get("category"))
        for item in projected if _text(item.get("category"))
    },key=str.lower)
    counts={
        "all":len(projected),
        "discover":sum(1 for x in projected if x["available"] and not x["installed"]),
        "installed":sum(1 for x in projected if x["installed"]),
        "updates":sum(1 for x in projected if x["update_available"]),
        "attention":sum(1 for x in projected if x["attention"]),
        "vp3":sum(1 for x in projected if x["product_type"]=="vp3_optional_app"),
        "user":sum(1 for x in projected if x["app_class"]=="user"),
        "shared":sum(1 for x in projected if x["product_type"]=="private_shared_app"),
        "hosted":sum(1 for x in projected if bool((x.get("hosting") or {}).get("bound"))),
    }
    return {
        "contract":CONTRACT,
        "catalog_version":inventory.get("catalog_version"),
        "source":"embedded_vp3_plus_local_registry",
        "public_app_store":False,
        "first_party_catalog":True,
        "items":items,
        "count":len(items),
        "counts":counts,
        "categories":categories,
        "query":q,
        "category":category,
        "view":view_key,
    }


def item(app_key:str)->dict[str,Any]:
    key=_text(app_key).lower()
    data=catalog()
    for row in data["items"]:
        if row["app_key"]==key:
            return row
    raise AppCenterError("App not found.",404)


def update_plan()->dict[str,Any]:
    data=catalog(view="updates")
    updates=[]
    blocked=0
    warnings=0
    for row in data["items"]:
        ready=row["readiness"]
        if ready["blocked"]: blocked+=1
        if ready["warnings"]: warnings+=1
        updates.append({
            "app_key":row["app_key"],
            "name":row["name"],
            "installed_version":row["installed_version"],
            "available_version":row["available_version"],
            "release_notes":row["release_notes"],
            "readiness":ready,
        })
    return {
        "contract":"vp3.homeserver.app-center.update-plan.v1",
        "updates":updates,
        "count":len(updates),
        "ready_count":sum(1 for x in updates if x["readiness"]["ready"]),
        "blocked_count":blocked,
        "warning_count":warnings,
        "batch_execution":False,
        "execution_authority":"homeserver_prebuilt_installer",
    }


def brain_context(limit:int=20)->dict[str,Any]:
    bounded=max(1,min(int(limit),50))
    data=catalog()
    attention=[row for row in data["items"] if row["attention"]][:bounded]
    updates=[row for row in data["items"] if row["update_available"]][:bounded]
    discover=[row for row in data["items"] if row["available"] and not row["installed"]][:bounded]
    def small(row:dict[str,Any])->dict[str,Any]:
        return {
            "app_key":row["app_key"],"name":row["name"],"category":row["category"],
            "installed_version":row["installed_version"],"available_version":row["available_version"],
            "update_available":row["update_available"],"attention":row["attention"],
            "readiness":{
                "operation":row["readiness"]["operation"],
                "ready":row["readiness"]["ready"],
                "blocked":row["readiness"]["blocked"],
                "warnings":row["readiness"]["warnings"],
            },
        }
    return {
        "contract":"vp3.homeserver.app-center.brain-context.v1",
        "summary":data["counts"],
        "updates":[small(x) for x in updates],
        "attention":[small(x) for x in attention],
        "discover":[small(x) for x in discover],
        "governance":{
            "read_only_context":True,
            "installs_execute_via_existing_prebuilt_installer":True,
            "write_actions_require_owner_approval":True,
            "homeserver_execution_authority":True,
            "public_app_store":False,
        },
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "catalog_discovery":True,
        "search":True,
        "categories":True,
        "install_readiness":True,
        "update_plan":True,
        "release_notes":True,
        "compatibility_attention":True,
        "agent_brain_context":True,
        "canonical_install_engine":"vp3.app.prebuilt-catalog.v1",
        "canonical_registry":"vp3.app.manager.v1",
        "batch_update_execution":False,
        "public_app_store":False,
        "first_party_catalog":True,
    }

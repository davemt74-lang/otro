from __future__ import annotations

from typing import Any

from . import (
    homeserver_app_distribution,
    homeserver_app_prebuilt,
    homeserver_app_releases,
    homeserver_app_resources,
    homeserver_app_security,
    homeserver_apps,
    hosting_cloud_control,
)

CONTRACT="vp3.app.manager.v1"


def _safe(call,default=None):
    try:
        return call()
    except Exception:
        return default


def _hosting_by_app()->dict[str,list[dict[str,Any]]]:
    rows=_safe(hosting_cloud_control.list_bound_sites,[]) or []
    out:dict[str,list[dict[str,Any]]]={}
    for row in rows:
        key=str((row or {}).get("target_app_key") or "")
        if key:
            out.setdefault(key,[]).append(dict(row))
    return out


def inventory()->dict[str,Any]:
    registry=homeserver_apps.list_apps()
    catalog=homeserver_app_prebuilt.catalog()
    hosting=_hosting_by_app()
    installed_by_key={str(row["app_key"]):row for row in registry.get("apps",[]) if row}
    catalog_by_key={str(row["key"]):row for row in catalog.get("packages",[]) if row}
    keys=sorted(set(installed_by_key)|set(catalog_by_key))
    items=[]
    counts={"installed":0,"available":0,"system":0,"user":0,"updates":0,"shared":0,"running":0,"hosted":0}
    for key in keys:
        app=installed_by_key.get(key)
        package=catalog_by_key.get(key)
        system=bool((app and app.get("app_class")=="system") or package)
        installed=bool(app and app.get("installed_version"))
        available=bool(package)
        shared=None
        if app and app.get("app_class")=="user":
            shared=_safe(lambda:homeserver_app_distribution.installed_provenance(key))
            if shared and not shared.get("installed_from_private_distribution"):
                shared=None
        permissions=_safe(lambda:homeserver_app_security.permission_status(key)) if app else None
        resources=_safe(lambda:homeserver_app_resources.resource_status(key)) if app else None
        releases=_safe(lambda:homeserver_app_releases.list_releases(key)) if installed else None
        update_available=bool(package and package.get("update_available"))
        bound=list(hosting.get(key,[]))
        lifecycle=str((app or {}).get("lifecycle_state") or ("available" if available else "unknown"))
        item={
            "app_key":key,
            "name":str((app or {}).get("name") or (package or {}).get("name") or key),
            "app_class":str((app or {}).get("app_class") or ("system" if package else "user")),
            "protected_system_app":bool((app or {}).get("protected_system_app") or package),
            "installed":installed,
            "available":available,
            "lifecycle_state":lifecycle,
            "installed_version":(app or {}).get("installed_version"),
            "available_version":(package or {}).get("version"),
            "update_available":update_available,
            "category":str((package or {}).get("category") or ((app or {}).get("metadata") or {}).get("prebuilt_category") or ("User App" if not system else "System")),
            "description":str((package or {}).get("description") or ((app or {}).get("metadata") or {}).get("prebuilt_description") or ""),
            "source_type":str((app or {}).get("source_type") or ("vp3_embedded" if package else "")),
            "metadata":dict((app or {}).get("metadata") or {}),
            "catalog":package,
            "permissions":permissions,
            "resources":resources,
            "releases":releases,
            "distribution":shared,
            "hosting":{
                "bound":bool(bound),
                "count":len(bound),
                "sites":bound,
                "public":any(bool(row.get("public_routing")) for row in bound),
            },
            "actions":{
                "install":bool(package and not installed),
                "update":bool(package and update_available),
                "open":bool(installed and lifecycle=="running"),
                "manage":bool(app),
                "rollback":bool(releases and releases.get("previous_release_id")),
                "recover":bool(app and lifecycle in {"failed","degraded"}),
                "host":bool(system and installed),
                "export":bool(app and app.get("app_class")=="user"),
            },
        }
        items.append(item)
        if installed: counts["installed"]+=1
        elif available: counts["available"]+=1
        if item["app_class"]=="system": counts["system"]+=1
        else: counts["user"]+=1
        if update_available: counts["updates"]+=1
        if shared: counts["shared"]+=1
        if lifecycle=="running": counts["running"]+=1
        if bound: counts["hosted"]+=1
    return {
        "contract":CONTRACT,
        "items":items,
        "counts":counts,
        "catalog_version":catalog.get("catalog_version"),
        "first_party_library":True,
        "app_store":False,
        "canonical_registry":True,
        "canonical_release_engine":True,
        "canonical_permission_engine":True,
        "canonical_hosting_engine":True,
        "canonical_distribution_engine":True,
    }


def app(app_key:str)->dict[str,Any]:
    key=str(app_key or "").strip().lower()
    for row in inventory()["items"]:
        if row["app_key"]==key:
            return row
    raise homeserver_apps.HomeServerAppError("App not found.",404)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "unified_inventory":True,
        "vp3_system_library":True,
        "installed_user_apps":True,
        "private_share_provenance":True,
        "permissions":True,
        "storage_usage":True,
        "release_update_rollback":True,
        "hosting_bindings":True,
        "app_store":False,
    }

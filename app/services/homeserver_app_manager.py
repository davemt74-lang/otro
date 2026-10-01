from __future__ import annotations

from typing import Any

from . import (
    homeserver_app_control,
    homeserver_app_distribution,
    homeserver_app_prebuilt,
    homeserver_app_releases,
    homeserver_app_resources,
    homeserver_app_security,
    homeserver_apps,
    homeserver_download_manager,
    homeserver_media_library,
    homeserver_media_processor,
    homeserver_media_tools,
    homeserver_media_server,
    homeserver_music_server,
    homeserver_photo_library,
    homeserver_video_editor,
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
    catalog_by_key={str(row["key"]):row for row in catalog.get("packages",[]) if row}
    installed_by_key={}
    for row in registry.get("apps",[]):
        if not row:
            continue
        key=str(row["app_key"])
        meta=dict(row.get("metadata") or {})
        if row.get("app_class")=="user" or key in catalog_by_key or meta.get("prebuilt_app"):
            installed_by_key[key]=row
    keys=sorted(set(installed_by_key)|set(catalog_by_key))
    items=[]
    counts={"installed":0,"available":0,"system":0,"user":0,"updates":0,"shared":0,"running":0,"hosted":0,"agent_compatible":0}
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
        media_state=_safe(homeserver_media_server.status) if key==homeserver_media_server.APP_KEY and installed else None
        music_state=_safe(homeserver_music_server.status) if key==homeserver_music_server.APP_KEY and installed else None
        photo_state=_safe(homeserver_photo_library.status) if key==homeserver_photo_library.APP_KEY and installed else None
        download_state=_safe(homeserver_download_manager.status) if key==homeserver_download_manager.APP_KEY and installed else None
        library_state=_safe(homeserver_media_library.status) if key==homeserver_media_library.APP_KEY and installed else None
        processor_state=_safe(homeserver_media_processor.status) if key==homeserver_media_processor.APP_KEY and installed else None
        video_state=_safe(homeserver_video_editor.status) if key==homeserver_video_editor.APP_KEY and installed else None
        control_compat=_safe(lambda:homeserver_app_control.compatibility(key)) if installed else None
        control_manifest=_safe(lambda:homeserver_app_control.manifest(key)) if installed else None
        item={
            "app_key":key,
            "name":str((app or {}).get("name") or (package or {}).get("name") or key),
            "app_class":str((app or {}).get("app_class") or ("system" if package else "user")),
            "product_type":"vp3_optional_app" if package else ("private_shared_app" if shared else "user_app"),
            "core_homeserver_feature":False,
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
            "media_server":media_state,
            "music_server":music_state,
            "photo_library":photo_state,
            "download_manager":download_state,
            "media_library":library_state,
            "media_processor":processor_state,
            "media_tools":homeserver_media_tools.public_capability() if key==homeserver_media_processor.APP_KEY else None,
            "video_editor":video_state,
            "agent_control":{
                "complete":bool(installed and control_compat and control_compat.get("compatible")),
                "compatible":bool(control_compat and control_compat.get("compatible")),
                "compatibility":control_compat,
                "manifest":control_manifest,
                "lifecycle":bool(installed),
                "permissions":bool(app),
                "settings":bool(installed),
                "hosting":bool(installed),
                "releases":bool(installed),
            },
            "deployment_modes":list((package or {}).get("deployment_modes") or ["local","private_remote","hosted_subdomain","custom_domain"]),
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
                "host":bool(installed),
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
        if control_compat and control_compat.get("compatible"): counts["agent_compatible"]+=1
    return {
        "contract":CONTRACT,
        "items":items,
        "counts":counts,
        "catalog_version":catalog.get("catalog_version"),
        "first_party_library":True,
        "vp3_optional_apps":True,
        "core_homeserver_features_excluded":True,
        "deployment_modes":["local","private_remote","hosted_subdomain","custom_domain"],
        "app_store":False,
        "canonical_registry":True,
        "canonical_release_engine":True,
        "canonical_permission_engine":True,
        "canonical_hosting_engine":True,
        "canonical_distribution_engine":True,
        "homeserver_agent_complete_control":True,
        "agent_manifest_actions":True,
        "universal_control_contract":"vp3.app.agent-control.v3",
        "compatibility_status":True,
        "generic_settings_control":True,
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
        "vp3_apps_library":True,
        "core_homeserver_features_excluded":True,
        "deployment_modes":["local","private_remote","hosted_subdomain","custom_domain"],
        "installed_user_apps":True,
        "private_share_provenance":True,
        "permissions":True,
        "storage_usage":True,
        "release_update_rollback":True,
        "hosting_bindings":True,
        "homeserver_agent_complete_control":True,
        "agent_manifest_actions":True,
        "universal_control_contract":"vp3.app.agent-control.v3",
        "compatibility_status":True,
        "generic_settings_control":True,
        "app_store":False,
    }

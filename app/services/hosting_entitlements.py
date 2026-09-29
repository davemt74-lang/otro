from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import hosting_runtime

CONTRACT="vp3.hosting.entitlements.v1"
_RUNTIMES={"static","php"}


class EntitlementError(hosting_runtime.HostingError):
    pass


def _path()->Path:
    root=hosting_runtime.hosting_root().parent
    root.mkdir(parents=True,exist_ok=True)
    return root/"entitlements.json"


def _load()->dict[str,Any]|None:
    path=_path()
    if not path.is_file():
        return None
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise EntitlementError("Hosting entitlement snapshot is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("contract")!=CONTRACT:
        raise EntitlementError("Hosting entitlement snapshot is invalid.",500)
    return payload


def _write(payload:dict[str,Any])->None:
    path=_path()
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def _positive_int(payload:dict[str,Any],key:str,minimum:int=0)->int:
    value=payload.get(key)
    if isinstance(value,bool):
        raise EntitlementError(f"{key} must be an integer.")
    try:
        parsed=int(value)
    except (TypeError,ValueError) as exc:
        raise EntitlementError(f"{key} must be an integer.") from exc
    if parsed<minimum:
        raise EntitlementError(f"{key} must be at least {minimum}.")
    return parsed


def reconcile(payload:dict[str,Any])->dict[str,Any]:
    try:
        revision=int(payload.get("revision"))
    except (TypeError,ValueError) as exc:
        raise EntitlementError("revision must be an integer.") from exc
    if revision<1:
        raise EntitlementError("revision must be at least 1.")
    package_key=str(payload.get("package_key") or "").strip()[:120]
    if not package_key:
        raise EntitlementError("package_key is required.")
    allowed=payload.get("allowed_runtimes",["static"])
    if not isinstance(allowed,list) or not allowed:
        raise EntitlementError("allowed_runtimes must be a non-empty list.")
    runtimes=sorted({str(x).strip().lower() for x in allowed})
    if any(x not in _RUNTIMES for x in runtimes):
        raise EntitlementError("allowed_runtimes contains an unsupported runtime.")

    normalized={
        "contract":CONTRACT,
        "revision":revision,
        "package_key":package_key,
        "max_sites":_positive_int(payload,"max_sites"),
        "max_active_sites":_positive_int(payload,"max_active_sites"),
        "max_public_routes":_positive_int(payload,"max_public_routes"),
        "max_storage_bytes_per_site":_positive_int(payload,"max_storage_bytes_per_site"),
        "max_sqlite_bytes_per_site":_positive_int(payload,"max_sqlite_bytes_per_site"),
        "allowed_runtimes":runtimes,
    }
    current=_load()
    if current:
        current_revision=int(current.get("revision") or 0)
        if revision<current_revision:
            result=status()
            result["reconcile_result"]="stale_ignored"
            return result
        if revision==current_revision:
            comparable={k:v for k,v in current.items() if k not in {"observed_at"}}
            if comparable!=normalized:
                raise EntitlementError("Entitlement revision already exists with different limits.",409)
            result=status()
            result["reconcile_result"]="idempotent"
            return result
    _write(normalized)
    result=status()
    result["reconcile_result"]="applied"
    return result


def current()->dict[str,Any]|None:
    payload=_load()
    return dict(payload) if payload else None


def _counts()->dict[str,int]:
    from . import hosting_public
    sites=hosting_runtime.list_sites()
    active=sum(1 for s in sites if s.get("state")=="active")
    public=0
    for site in sites:
        try:
            route=hosting_public.route_status_for_site(str(site["site_id"]))
        except Exception:
            route=None
        if route and route.get("desired_state")=="active":
            public+=1
    return {"sites":len(sites),"active_sites":active,"public_routes":public}


def status()->dict[str,Any]:
    ent=_load()
    counts=_counts()
    if ent is None:
        return {
            "contract":CONTRACT,
            "configured":False,
            "counts":counts,
            "within_entitlement":True,
            "overages":[],
        }
    over=[]
    if counts["sites"]>int(ent["max_sites"]): over.append("sites")
    if counts["active_sites"]>int(ent["max_active_sites"]): over.append("active_sites")
    if counts["public_routes"]>int(ent["max_public_routes"]): over.append("public_routes")
    return {
        **{k:v for k,v in ent.items()},
        "configured":True,
        "counts":counts,
        "within_entitlement":not over,
        "overages":over,
    }


def enforce_site_request(*,runtime_kind:str,storage_limit_bytes:int|None,sqlite_limit_bytes:int|None,is_new:bool)->None:
    ent=_load()
    if ent is None:
        return
    runtime=str(runtime_kind or "").lower()
    if runtime not in set(ent["allowed_runtimes"]):
        raise EntitlementError("Hosting package does not allow this runtime.",403)
    counts=_counts()
    if is_new and counts["sites"]>=int(ent["max_sites"]):
        raise EntitlementError("Hosting package site limit reached.",403)
    if storage_limit_bytes is None or int(storage_limit_bytes)>int(ent["max_storage_bytes_per_site"]):
        raise EntitlementError("Requested storage limit exceeds the hosting package.",403)
    if sqlite_limit_bytes is None or int(sqlite_limit_bytes)>int(ent["max_sqlite_bytes_per_site"]):
        raise EntitlementError("Requested SQLite limit exceeds the hosting package.",403)


def enforce_activation(site_id:str)->None:
    ent=_load()
    if ent is None:
        return
    site=hosting_runtime.get_site(site_id)
    enforce_site_request(
        runtime_kind=str(site["runtime_kind"]),
        storage_limit_bytes=site.get("storage_limit_bytes"),
        sqlite_limit_bytes=site.get("sqlite_limit_bytes"),
        is_new=False,
    )
    counts=_counts()
    if site.get("state")!="active" and counts["active_sites"]>=int(ent["max_active_sites"]):
        raise EntitlementError("Hosting package active-site limit reached.",403)


def enforce_public_route(site_id:str)->None:
    ent=_load()
    if ent is None:
        return
    from . import hosting_public
    current_route=hosting_public.route_status_for_site(site_id)
    already_active=bool(current_route and current_route.get("desired_state")=="active")
    counts=_counts()
    if not already_active and counts["public_routes"]>=int(ent["max_public_routes"]):
        raise EntitlementError("Hosting package public-route limit reached.",403)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "cloud_authoritative_package":True,
        "monotonic_revisions":True,
        "site_limit_enforcement":True,
        "active_site_limit_enforcement":True,
        "public_route_limit_enforcement":True,
        "per_site_storage_cap":True,
        "per_site_sqlite_cap":True,
        "runtime_entitlements":True,
        "downgrade_deletes_data":False,
        "overage_is_non_destructive":True,
        "billing_engine":False,
    }

from __future__ import annotations

import json
import re
from typing import Any

from . import (
    hosting_cloud_control,
    hosting_deployment,
    hosting_entitlements,
    hosting_observability,
    hosting_public,
    hosting_recovery,
    hosting_runtime,
    hosting_serving,
    hosting_sqlite,
)

CONTRACT="vp3.hosting.operations.v1"
_ACTION_KEY=re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_ALLOWED_ACTIONS={"site.suspend","site.activate","deployment.rollback","recovery.create"}


class HostingOperationsError(hosting_runtime.HostingError):
    pass


def _site_summary(site:dict[str,Any])->dict[str,Any]:
    site_id=str(site["site_id"])
    deployment=hosting_deployment.deployment_status(site_id)
    recovery=hosting_recovery.recovery_health(site_id)
    route=hosting_public.route_status_for_site(site_id)
    sqlite=hosting_sqlite.schema_status(site_id)
    serving=hosting_serving.runtime_health(site_id)
    usage=hosting_runtime.measure_usage(site_id)
    binding=hosting_cloud_control.binding_for_site(site_id)
    observability=hosting_observability.summary(site_id)
    return {
        "site_id":site_id,
        "display_name":site["display_name"],
        "hostname":site.get("requested_hostname"),
        "runtime_kind":site["runtime_kind"],
        "state":site["state"],
        "usage":usage,
        "storage_limit_bytes":site.get("storage_limit_bytes"),
        "sqlite_limit_bytes":site.get("sqlite_limit_bytes"),
        "active_release_id":deployment.get("active_release_id"),
        "previous_release_id":deployment.get("previous_release_id"),
        "serving_ready":bool(serving.get("local_serving_ready")),
        "runtime_scheduler":serving.get("scheduler") or {},
        "observability":observability,
        "sqlite_healthy":bool(sqlite.get("healthy")),
        "recovery_points":recovery.get("recovery_points"),
        "latest_recovery_verified":recovery.get("latest_verified"),
        "public_route_ready":bool(route and route.get("route_ready")),
        "public_url":route.get("public_url") if route else None,
        "cloud_bound":bool(binding),
        "cloud_revision":int(binding.get("revision") or 0) if binding else None,
        "cloud_desired_state":binding.get("desired_state") if binding else None,
    }


def dashboard()->dict[str,Any]:
    sites=[_site_summary(site) for site in hosting_runtime.list_sites()]
    counts={
        "sites":len(sites),
        "active":sum(1 for s in sites if s["state"]=="active"),
        "suspended":sum(1 for s in sites if s["state"]=="suspended"),
        "failed":sum(1 for s in sites if s["state"]=="failed"),
        "public_routes":sum(1 for s in sites if s["public_route_ready"]),
        "serving_ready":sum(1 for s in sites if s["serving_ready"]),
        "sqlite_healthy":sum(1 for s in sites if s["sqlite_healthy"]),
        "runtime_inflight":sum(int((s.get("runtime_scheduler") or {}).get("inflight") or 0) for s in sites),
        "runtime_rejected":sum(int((s.get("runtime_scheduler") or {}).get("rejected") or 0) for s in sites),
        "requests_total":sum(int((s.get("observability") or {}).get("requests_total") or 0) for s in sites),
        "server_errors_total":sum(int((s.get("observability") or {}).get("server_error_total") or 0) for s in sites),
    }
    issues=[]
    for s in sites:
        if s["state"] in {"failed","suspended"}:
            issues.append({"site_id":s["site_id"],"code":"site_"+s["state"]})
        if not s["sqlite_healthy"]:
            issues.append({"site_id":s["site_id"],"code":"sqlite_unhealthy"})
        if s["state"]=="active" and not s["serving_ready"]:
            issues.append({"site_id":s["site_id"],"code":"serving_not_ready"})
        if s["public_url"] and not s["public_route_ready"]:
            issues.append({"site_id":s["site_id"],"code":"public_route_not_ready"})
    return {
        "contract":CONTRACT,
        "counts":counts,
        "sites":sites,
        "issues":issues,
        "entitlements":hosting_entitlements.status(),
        "healthy":not issues,
    }


def _event_exists(site_id:str,action:str,idempotency_key:str)->dict[str,Any]|None:
    from ..database import db
    with db() as connection:
        rows=connection.execute(
            "SELECT details_json FROM hosting_runtime_events WHERE site_id=? AND event_type='hosting.operation' ORDER BY id DESC LIMIT 200",
            (site_id,),
        ).fetchall()
    for row in rows:
        try:
            details=json.loads(row["details_json"] or "{}")
        except Exception:
            continue
        if details.get("idempotency_key")!=idempotency_key:
            continue
        if details.get("action")!=action:
            raise HostingOperationsError("Idempotency key was already used for a different hosting action.",409)
        result=details.get("result")
        return result if isinstance(result,dict) else None
    return None


def _record(site_id:str,action:str,idempotency_key:str,result:dict[str,Any])->None:
    from ..database import db
    safe={
        "action":action,
        "idempotency_key":idempotency_key,
        "result":result,
    }
    with db() as connection:
        connection.execute(
            "INSERT INTO hosting_runtime_events(site_id,event_type,state,details_json) VALUES (?,?,?,?)",
            (site_id,"hosting.operation",hosting_runtime.get_site(site_id)["state"],json.dumps(safe,separators=(",",":"),sort_keys=True)),
        )


def execute(site_id:str,action:str,idempotency_key:str,*,confirmed:bool=False)->dict[str,Any]:
    hosting_runtime.get_site(site_id)
    action=str(action or "").strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise HostingOperationsError("Unsupported hosting operation.",403)
    key=str(idempotency_key or "").strip()
    if not _ACTION_KEY.fullmatch(key):
        raise HostingOperationsError("A valid idempotency key is required.")

    replay=_event_exists(site_id,action,key)
    if replay is not None:
        return {**replay,"replayed":True}

    if action in {"site.suspend","site.activate","deployment.rollback"} and not confirmed:
        raise HostingOperationsError("This hosting operation requires explicit confirmation.",409)

    if action=="site.activate":
        binding=hosting_cloud_control.binding_for_site(site_id)
        if binding and str(binding.get("desired_state") or "")!="active":
            raise HostingOperationsError("Cloud desired state does not permit local activation.",409)

    if action=="site.suspend":
        site=hosting_runtime.set_state(site_id,"suspended")
        result={"site_id":site_id,"action":action,"state":site["state"]}
    elif action=="site.activate":
        site=hosting_runtime.set_state(site_id,"active")
        result={"site_id":site_id,"action":action,"state":site["state"]}
    elif action=="deployment.rollback":
        release=hosting_deployment.rollback(site_id)
        result={
            "site_id":site_id,
            "action":action,
            "release_id":release.get("release_id"),
            "previous_release_id":release.get("previous_release_id"),
        }
    else:
        recovery=hosting_recovery.create_recovery_point(site_id,reason="governed-operation")
        result={
            "site_id":site_id,
            "action":action,
            "recovery_id":recovery.get("recovery_id"),
            "verified":recovery.get("verified"),
        }

    _record(site_id,action,key,result)
    return {**result,"replayed":False}


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "read_only_dashboard":True,
        "safe_actions":sorted(_ALLOWED_ACTIONS),
        "idempotent_actions":True,
        "uses_canonical_services":True,
        "raw_filesystem_access":False,
        "raw_sql_access":False,
        "billing_mutations":False,
        "explicit_confirmation_for_consequential_actions":True,
        "cloud_desired_state_activation_gate":True,
        "automatic_destructive_actions":False,
    }

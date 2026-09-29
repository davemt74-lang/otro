from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import hosting_runtime, hosting_serving

CONTRACT="vp3.hosting.public-route.v1"
_STATES={"active","inactive"}
_TLS_STATES={"pending","active","renewing","failed"}


class PublicRoutingError(hosting_runtime.HostingError):
    pass


def _normalize_hostname(value:str)->str:
    host=str(value or "").strip().lower().rstrip(".")
    if ":" in host:
        raise PublicRoutingError("Public hostname must not include a port.")
    if not hosting_runtime._HOSTNAME.fullmatch(host):
        raise PublicRoutingError("Public hostname is invalid.")
    return host


def _route_path(site_id:str)->Path:
    return hosting_runtime.site_root(site_id)/"public-route.json"


def _load(site_id:str)->dict[str,Any]|None:
    path=_route_path(site_id)
    if not path.is_file():
        return None
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise PublicRoutingError("Public route metadata is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("contract")!=CONTRACT or payload.get("site_id")!=site_id:
        raise PublicRoutingError("Public route metadata is invalid.",500)
    return payload


def _write(site_id:str,payload:dict[str,Any])->None:
    path=_route_path(site_id)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def _parse_not_after(value:Any)->str|None:
    raw=str(value or "").strip()
    if not raw:
        return None
    candidate=raw[:-1]+"+00:00" if raw.endswith("Z") else raw
    try:
        dt=datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise PublicRoutingError("certificate_not_after must be an ISO-8601 timestamp.") from exc
    if dt.tzinfo is None:
        raise PublicRoutingError("certificate_not_after must include a timezone.")
    return dt.astimezone(timezone.utc).isoformat()


def _tls_valid(payload:dict[str,Any])->bool:
    if str(payload.get("tls_state") or "") not in {"active","renewing"}:
        return False
    raw=payload.get("certificate_not_after")
    if not raw:
        return False
    try:
        dt=datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if dt.tzinfo is None:
        return False
    return dt.astimezone(timezone.utc)>datetime.now(timezone.utc)


def _find_by_hostname(hostname:str)->tuple[str,dict[str,Any]]|None:
    wanted=_normalize_hostname(hostname)
    for site in hosting_runtime.list_sites():
        site_id=str(site["site_id"])
        route=_load(site_id)
        if route and route.get("hostname")==wanted:
            return site_id,route
    return None


def reconcile(
    cloud_site_id:str,
    *,
    revision:int,
    hostname:str,
    desired_state:str,
    hostname_verified:bool,
    tls_state:str,
    certificate_not_after:str|None,
    rotate_token:bool=False,
)->dict[str,Any]:
    from . import hosting_cloud_control

    cloud=hosting_cloud_control.status(cloud_site_id)
    site_id=str(cloud["site_id"])
    binding=hosting_cloud_control.binding_for_site(site_id)
    if not binding:
        raise PublicRoutingError("Cloud hosting binding not found.",404)
    if int(revision)!=int(binding.get("revision") or 0):
        raise PublicRoutingError("Public route revision does not match current Cloud desired-state revision.",409)

    host=_normalize_hostname(hostname)
    assigned=str(cloud.get("requested_hostname") or "").strip().lower()
    if not assigned or host!=assigned:
        raise PublicRoutingError("Public route hostname does not match the Cloud-assigned hostname.",409)

    state=str(desired_state or "").strip().lower()
    if state not in _STATES:
        raise PublicRoutingError("Public route desired_state is invalid.")
    tls=str(tls_state or "").strip().lower()
    if tls not in _TLS_STATES:
        raise PublicRoutingError("Public route tls_state is invalid.")
    verified=bool(hostname_verified)
    not_after=_parse_not_after(certificate_not_after)

    existing_host=_find_by_hostname(host)
    if existing_host and existing_host[0]!=site_id and state=="active":
        other=existing_host[1]
        if other.get("desired_state")=="active":
            raise PublicRoutingError("Public hostname is already assigned to another hosted site.",409)

    current=_load(site_id)
    if current and int(current.get("revision") or 0)>int(revision):
        return _projection(site_id,current,reconcile_result="stale_ignored")
    if current and int(current.get("revision") or 0)==int(revision):
        same=(
            current.get("hostname")==host
            and current.get("desired_state")==state
            and bool(current.get("hostname_verified"))==verified
            and current.get("tls_state")==tls
            and current.get("certificate_not_after")==not_after
        )
        if not same:
            raise PublicRoutingError("Public route revision already exists with different state.",409)

    if state=="active":
        if not verified:
            raise PublicRoutingError("Public route cannot activate before hostname ownership is verified.",409)
        if tls not in {"active","renewing"} or not not_after:
            raise PublicRoutingError("Public route cannot activate without valid Cloud-edge TLS.",409)

    token=(current or {}).get("route_token")
    if not token or rotate_token:
        token=secrets.token_urlsafe(32)

    payload={
        "contract":CONTRACT,
        "site_id":site_id,
        "cloud_site_id":cloud_site_id,
        "revision":int(revision),
        "hostname":host,
        "desired_state":state,
        "hostname_verified":verified,
        "tls_state":tls,
        "certificate_not_after":not_after,
        "tls_termination":"cloud_edge",
        "route_token":token,
        "route_token_sha256":hashlib.sha256(token.encode("utf-8")).hexdigest(),
    }
    _write(site_id,payload)
    result=_projection(site_id,payload,reconcile_result="idempotent" if current else "applied")
    result["route_token"]=token
    return result


def _projection(site_id:str,payload:dict[str,Any],*,reconcile_result:str|None=None)->dict[str,Any]:
    health=hosting_serving.runtime_health(site_id)
    tls_ok=_tls_valid(payload)
    ready=(
        payload.get("desired_state")=="active"
        and bool(payload.get("hostname_verified"))
        and tls_ok
        and bool(health.get("local_serving_ready"))
        and health.get("site_state")=="active"
    )
    result={
        "contract":CONTRACT,
        "cloud_site_id":payload.get("cloud_site_id"),
        "site_id":site_id,
        "revision":int(payload.get("revision") or 0),
        "hostname":payload.get("hostname"),
        "desired_state":payload.get("desired_state"),
        "hostname_verified":bool(payload.get("hostname_verified")),
        "tls_state":payload.get("tls_state"),
        "certificate_not_after":payload.get("certificate_not_after"),
        "tls_termination":"cloud_edge",
        "tls_valid":tls_ok,
        "local_serving_ready":bool(health.get("local_serving_ready")),
        "route_ready":ready,
        "public_url":f"https://{payload.get('hostname')}" if payload.get("hostname") else None,
        "home_server_public_listener":False,
    }
    if reconcile_result:
        result["reconcile_result"]=reconcile_result
    return result


def route_status_for_site(site_id:str)->dict[str,Any]|None:
    hosting_runtime.get_site(site_id)
    payload=_load(site_id)
    return _projection(site_id,payload) if payload else None


def status(cloud_site_id:str)->dict[str,Any]:
    from . import hosting_cloud_control
    cloud=hosting_cloud_control.status(cloud_site_id)
    route=route_status_for_site(str(cloud["site_id"]))
    if route is None:
        return {
            "contract":CONTRACT,
            "cloud_site_id":cloud_site_id,
            "site_id":cloud["site_id"],
            "hostname":cloud.get("requested_hostname"),
            "desired_state":"inactive",
            "hostname_verified":False,
            "tls_state":"pending",
            "certificate_not_after":None,
            "tls_termination":"cloud_edge",
            "tls_valid":False,
            "local_serving_ready":bool(cloud.get("local_serving_ready")),
            "route_ready":False,
            "public_url":None,
            "home_server_public_listener":False,
        }
    return route


def authorize_ingress(hostname:str,route_token:str,forwarded_proto:str)->dict[str,Any]:
    host=_normalize_hostname(hostname)
    found=_find_by_hostname(host)
    if not found:
        raise PublicRoutingError("Public hosting route not found.",404)
    site_id,payload=found
    if payload.get("desired_state")!="active":
        raise PublicRoutingError("Public hosting route is inactive.",503)
    if not bool(payload.get("hostname_verified")):
        raise PublicRoutingError("Public hosting hostname is not verified.",503)
    if str(forwarded_proto or "").strip().lower()!="https":
        raise PublicRoutingError("Public hosting ingress requires HTTPS at the Cloud edge.",426)
    if not _tls_valid(payload):
        raise PublicRoutingError("Public hosting TLS is unavailable or expired.",503)
    supplied=str(route_token or "")
    expected=str(payload.get("route_token_sha256") or "")
    actual=hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    if not supplied or not secrets.compare_digest(actual,expected):
        raise PublicRoutingError("Public hosting route authorization failed.",403)
    health=hosting_serving.runtime_health(site_id)
    if not health.get("local_serving_ready") or health.get("site_state")!="active":
        raise PublicRoutingError("Hosted site is not ready for public serving.",503)
    return {"site_id":site_id,"hostname":host}


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "cloud_authoritative_hostname":True,
        "cloud_edge_tls":True,
        "hostname_ownership_required":True,
        "hostname_collision_protection":True,
        "https_required":True,
        "certificate_expiry_gate":True,
        "route_token_binding":True,
        "route_token_status_redacted":True,
        "safe_deactivation":True,
        "home_server_public_listener":False,
        "loopback_ingress_only":True,
        "raw_certificate_private_key_on_homeserver":False,
    }

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ..database import db
from . import hosting_deployment, hosting_runtime, hosting_serving

CONTRACT="vp3.hosting.cloud-control.v1"
_CLOUD_SITE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
_STATES={"configured","active","suspended"}


class CloudHostingError(hosting_runtime.HostingError):
    pass


def _binding_path(site_id: str) -> Path:
    return hosting_runtime.site_root(site_id)/"cloud-binding.json"


def _canonical_desired(payload: dict[str,Any]) -> dict[str,Any]:
    cloud_site_id=str(payload.get("cloud_site_id") or "").strip()
    if not _CLOUD_SITE.fullmatch(cloud_site_id):
        raise CloudHostingError("cloud_site_id is invalid.")
    try:
        revision=int(payload.get("revision"))
    except (TypeError,ValueError) as exc:
        raise CloudHostingError("revision must be an integer.") from exc
    if revision<1:
        raise CloudHostingError("revision must be at least 1.")
    name=" ".join(str(payload.get("display_name") or "").split())[:160]
    if not name:
        raise CloudHostingError("display_name is required.")
    hostname=str(payload.get("requested_hostname") or "").strip().lower() or None
    if hostname is not None and not hosting_runtime._HOSTNAME.fullmatch(hostname):
        raise CloudHostingError("requested_hostname is invalid.")
    runtime=str(payload.get("runtime_kind") or "static").strip().lower()
    if runtime not in {"static","php"}:
        raise CloudHostingError("runtime_kind must be static or php.")
    state=str(payload.get("desired_state") or "configured").strip().lower()
    if state not in _STATES:
        raise CloudHostingError("desired_state is invalid.")

    def optional_limit(key:str)->int|None:
        value=payload.get(key)
        if value is None:
            return None
        if isinstance(value,bool):
            raise CloudHostingError(f"{key} must be an integer.")
        try:
            parsed=int(value)
        except (TypeError,ValueError) as exc:
            raise CloudHostingError(f"{key} must be an integer.") from exc
        if parsed<0:
            raise CloudHostingError(f"{key} cannot be negative.")
        return parsed

    return {
        "cloud_site_id":cloud_site_id,
        "revision":revision,
        "display_name":name,
        "requested_hostname":hostname,
        "runtime_kind":runtime,
        "desired_state":state,
        "storage_limit_bytes":optional_limit("storage_limit_bytes"),
        "sqlite_limit_bytes":optional_limit("sqlite_limit_bytes"),
    }


def _fingerprint(desired:dict[str,Any])->str:
    material={k:v for k,v in desired.items() if k!="revision"}
    return hashlib.sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def _load_binding(site_id:str)->dict[str,Any]|None:
    path=_binding_path(site_id)
    if not path.is_file():
        return None
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise CloudHostingError("Cloud hosting binding is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("contract")!=CONTRACT or payload.get("site_id")!=site_id:
        raise CloudHostingError("Cloud hosting binding is invalid.",500)
    return payload


def _write_binding(site_id:str,binding:dict[str,Any])->None:
    path=_binding_path(site_id)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(binding,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def _find_binding(cloud_site_id:str)->tuple[dict[str,Any],dict[str,Any]]|None:
    for site in hosting_runtime.list_sites():
        binding=_load_binding(str(site["site_id"]))
        if binding and binding.get("cloud_site_id")==cloud_site_id:
            return site,binding
    return None


def _site_projection(site:dict[str,Any],binding:dict[str,Any])->dict[str,Any]:
    deployment=hosting_deployment.deployment_status(str(site["site_id"]))
    health=hosting_serving.runtime_health(str(site["site_id"]))
    return {
        "contract":CONTRACT,
        "cloud_site_id":binding["cloud_site_id"],
        "site_id":site["site_id"],
        "revision":int(binding["revision"]),
        "desired_state":binding["desired_state"],
        "observed_state":site["state"],
        "display_name":site["display_name"],
        "requested_hostname":site.get("requested_hostname"),
        "runtime_kind":site["runtime_kind"],
        "storage_limit_bytes":site.get("storage_limit_bytes"),
        "sqlite_limit_bytes":site.get("sqlite_limit_bytes"),
        "active_release_id":deployment.get("active_release_id"),
        "local_serving_ready":bool(health.get("local_serving_ready")),
        "sqlite_healthy":bool((health.get("sqlite") or {}).get("healthy")),
        "public_routing":False,
    }


def list_bound_sites()->list[dict[str,Any]]:
    items=[]
    for site in hosting_runtime.list_sites():
        binding=_load_binding(str(site["site_id"]))
        if binding:
            items.append(_site_projection(site,binding))
    items.sort(key=lambda item:str(item["cloud_site_id"]))
    return items


def status(cloud_site_id:str)->dict[str,Any]:
    candidate=str(cloud_site_id or "").strip()
    if not _CLOUD_SITE.fullmatch(candidate):
        raise CloudHostingError("cloud_site_id is invalid.")
    found=_find_binding(candidate)
    if not found:
        raise CloudHostingError("Cloud-bound hosted site not found.",404)
    return _site_projection(found[0],found[1])


def reconcile(payload:dict[str,Any])->dict[str,Any]:
    desired=_canonical_desired(payload)
    fingerprint=_fingerprint(desired)
    found=_find_binding(desired["cloud_site_id"])

    if found is None:
        site=hosting_runtime.create_site(
            desired["display_name"],
            requested_hostname=desired["requested_hostname"],
            runtime_kind=desired["runtime_kind"],
            storage_limit_bytes=desired["storage_limit_bytes"],
            sqlite_limit_bytes=desired["sqlite_limit_bytes"],
        )
        binding={
            "contract":CONTRACT,
            "cloud_site_id":desired["cloud_site_id"],
            "site_id":site["site_id"],
            "revision":desired["revision"],
            "desired_state":desired["desired_state"],
            "desired_fingerprint":fingerprint,
        }
        _write_binding(str(site["site_id"]),binding)
    else:
        site,binding=found
        current_revision=int(binding.get("revision") or 0)
        current_fingerprint=str(binding.get("desired_fingerprint") or "")
        if desired["revision"]<current_revision:
            result=_site_projection(site,binding)
            result["reconcile_result"]="stale_ignored"
            return result
        if desired["revision"]==current_revision:
            if current_fingerprint!=fingerprint:
                raise CloudHostingError("Revision already exists with different desired state.",409)
            result=_site_projection(site,binding)
            result["reconcile_result"]="idempotent"
            return result
        if str(site["runtime_kind"])!=desired["runtime_kind"]:
            raise CloudHostingError("Runtime kind cannot be changed after site creation.",409)
        with db() as connection:
            connection.execute(
                """
                UPDATE hosting_sites
                SET display_name=?,requested_hostname=?,storage_limit_bytes=?,sqlite_limit_bytes=?,updated_at=CURRENT_TIMESTAMP
                WHERE site_id=?
                """,
                (
                    desired["display_name"],desired["requested_hostname"],
                    desired["storage_limit_bytes"],desired["sqlite_limit_bytes"],site["site_id"],
                ),
            )
        binding={
            "contract":CONTRACT,
            "cloud_site_id":desired["cloud_site_id"],
            "site_id":site["site_id"],
            "revision":desired["revision"],
            "desired_state":desired["desired_state"],
            "desired_fingerprint":fingerprint,
        }
        _write_binding(str(site["site_id"]),binding)

    site=hosting_runtime.get_site(str(binding["site_id"]))
    desired_state=desired["desired_state"]
    if desired_state=="active":
        deployment=hosting_deployment.deployment_status(str(site["site_id"]))
        if not deployment.get("active_release_id"):
            # A desired public/active state never fabricates readiness.
            observed="configured"
            if site["state"]!="configured":
                hosting_runtime.set_state(str(site["site_id"]),"configured")
        else:
            hosting_runtime.set_state(str(site["site_id"]),"active")
            observed="active"
    else:
        hosting_runtime.set_state(str(site["site_id"]),desired_state)
        observed=desired_state

    result=_site_projection(hosting_runtime.get_site(str(site["site_id"])),binding)
    result["reconcile_result"]="applied"
    if desired_state=="active" and observed!="active":
        result["blocked_reason"]="deployment_required"
    with db() as connection:
        connection.execute(
            "INSERT INTO hosting_runtime_events(site_id,event_type,state,details_json) VALUES (?,?,?,?)",
            (
                site["site_id"],"cloud.reconciled",result["observed_state"],
                json.dumps({
                    "cloud_site_id":desired["cloud_site_id"],
                    "revision":desired["revision"],
                    "desired_state":desired_state,
                    "reconcile_result":result["reconcile_result"],
                    "blocked_reason":result.get("blocked_reason"),
                },separators=(",",":"),sort_keys=True),
            ),
        )
    return result


def inventory()->dict[str,Any]:
    items=list_bound_sites()
    return {
        "contract":CONTRACT,
        "sites":items,
        "count":len(items),
        "cloud_authoritative_identity":True,
        "homeserver_authoritative_runtime":True,
        "public_routing":False,
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "cloud_authoritative_identity":True,
        "homeserver_authoritative_runtime":True,
        "desired_state_reconciliation":True,
        "monotonic_revisions":True,
        "idempotent_replay":True,
        "stale_revision_ignored":True,
        "runtime_kind_immutable":True,
        "active_requires_deployment":True,
        "filesystem_paths_remote":False,
        "sqlite_remote_access":False,
        "public_routing":False,
    }

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import fleet_management, tracky_federation_access_operations, tracky_federation_agent_health, tracky_federation_operations

FEDERATION_FLEET_HEALTH_PROTOCOL = "physical_federation_fleet_health.v1"
TRACKY_FEDERATION_FLEET_HEALTH_VERSION = "2.80"
_STATES = ("healthy","degraded","stale","recovering","offline","failed","unknown")
_RANK = {"healthy":0,"unknown":1,"degraded":2,"stale":3,"recovering":4,"offline":5,"failed":6}

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp()*1000)

def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:max(1,limit)]

def _site_id(value: Any) -> str:
    return _text(value,64).lower()

def _epoch_ms(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        n=float(value)
        if n>100000000000:
            return int(n)
    except (TypeError,ValueError):
        pass
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if parsed.tzinfo is None:
            parsed=parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp()*1000)
    except ValueError:
        return 0

def _severity(state: str) -> str:
    if state in {"failed","offline"}:
        return "critical"
    if state in {"degraded","stale","recovering"}:
        return "warning"
    return "info"

def _device_state(device: dict[str,Any], now_ms: int, stale_after_ms: int, offline_after_ms: int) -> tuple[str,list[str],int]:
    issues=[_text(v,80).lower() for v in list(device.get("issues") or [])[:32] if _text(v,80)]
    if device.get("privacy_fault"): issues.append("privacy_fault")
    commissioning=_text(device.get("commissioning_state"),24).lower()
    certification=_text(device.get("certification_result"),24).lower()
    backup=_text(device.get("backup_state"),24).lower()
    storage=_text(device.get("storage_state"),24).lower()
    update=_text(device.get("update_status"),32).lower()
    if commissioning=="blocked": issues.append("commissioning_blocked")
    elif commissioning=="degraded": issues.append("commissioning_degraded")
    if certification=="failed": issues.append("certification_failed")
    elif certification=="degraded": issues.append("certification_degraded")
    if backup in {"missing","stale"}: issues.append("backup_"+backup)
    if storage in {"low","critical"}: issues.append("storage_"+storage)
    if update in {"failed","rolled_back"}: issues.append("update_"+update)
    if int(device.get("watchdog_failures") or 0)>0: issues.append("watchdog_recovery")
    issues=list(dict.fromkeys(issues))
    seen=_epoch_ms(device.get("last_seen_at") or device.get("reported_at"))
    age=max(0,now_ms-seen) if seen else 0
    if seen and age>offline_after_ms:
        return "offline", list(dict.fromkeys([*issues,"telemetry_offline"])), age
    if seen and age>stale_after_ms:
        return "stale", list(dict.fromkeys([*issues,"telemetry_stale"])), age
    explicit=_text(device.get("health") or device.get("state"),32).lower()
    fatal={"privacy_fault","commissioning_blocked","certification_failed","storage_critical","update_failed"}
    if any(code in fatal for code in issues) or explicit in {"critical","failed","error"}:
        return "failed", issues, age
    if issues or explicit in {"warning","degraded"}:
        return "degraded", issues, age
    if explicit in {"offline","disconnected"}:
        return "offline", list(dict.fromkeys([*issues,"runtime_offline"])), age
    if explicit in {"healthy","ready","online","current",""}:
        return "healthy", issues, age
    return "unknown", issues, age

def _local_fleet_site(local_site_id: str, now_ms: int, stale_after_ms: int, offline_after_ms: int) -> dict[str,Any]:
    settings=fleet_management.get_settings()
    diagnostics_allowed=bool(settings.get("enabled") and settings.get("remote_diagnostics"))
    snapshot=fleet_management.remote_diagnostics_summary() if diagnostics_allowed else fleet_management.local_device_snapshot()
    state,issues,age=_device_state(snapshot,now_ms,stale_after_ms,offline_after_ms)
    device={
        "device_id":_text(snapshot.get("device_id"),80),
        "label":_text(snapshot.get("label") or "HomeServer",120),
        "site_id":local_site_id,
        "hardware_profile":_text(snapshot.get("profile_key") or snapshot.get("experience_profile") or "custom",60).lower(),
        "os_version":_text(snapshot.get("os_version"),80),
        "hardware_experience_version":_text(snapshot.get("hardware_experience_version"),80),
        "release_channel":_text(snapshot.get("release_channel"),24).lower(),
        "rollout_ring":_text(snapshot.get("rollout_ring"),24).lower(),
        "commissioning_state":_text(snapshot.get("commissioning_state"),24).lower(),
        "certification_result":_text(snapshot.get("certification_result"),24).lower(),
        "update_status":_text(snapshot.get("update_status"),32).lower(),
        "backup_state":_text(snapshot.get("backup_state"),24).lower(),
        "storage_state":_text(snapshot.get("storage_state"),24).lower(),
        "watchdog_failures":max(0,min(int(snapshot.get("watchdog_failures") or 0),1000)),
        "privacy_fault":bool(snapshot.get("privacy_fault")),
        "last_seen_at":snapshot.get("reported_at"),
        "stale_age_ms":age,
        "state":state,
        "severity":_severity(state),
        "issues":issues,
    }
    return {
        "site_id":local_site_id,
        "generated_at_ms":now_ms,
        "diagnostics_allowed":diagnostics_allowed,
        "devices":[device] if diagnostics_allowed else [],
    }

def build_report(operations: dict[str,Any], federation_health: dict[str,Any], access: dict[str,Any], *, now_ms: int | None = None) -> dict[str,Any]:
    now_ms=int(now_ms if now_ms is not None else _now_ms())
    settings=fleet_management.get_settings()
    stale_after_ms=max(60000,int(settings.get("stale_after_seconds") or 300)*1000)
    offline_after_ms=max(stale_after_ms*2,stale_after_ms*3)
    local=_site_id(operations.get("local_site_id") or federation_health.get("local_site_id"))
    fleet_site=_local_fleet_site(local,now_ms,stale_after_ms,offline_after_ms) if local else None
    fed_by={_site_id(row.get("site_id")):row for row in list(federation_health.get("sites") or []) if isinstance(row,dict)}
    access_by={_site_id(row.get("site_id")):row for row in list(access.get("peers") or []) if isinstance(row,dict)}
    sites=[]
    for raw in list(operations.get("sites") or []):
        if not isinstance(raw,dict):
            continue
        site=_site_id(raw.get("id") or raw.get("site_id"))
        if not site:
            continue
        fed=fed_by.get(site,{})
        visible=site==local or bool(access_by.get(site,{}).get("policy_peer_allowed"))
        devices=list(fleet_site.get("devices") or []) if fleet_site and site==local else []
        if devices:
            local_state=max((str(d.get("state") or "unknown") for d in devices),key=lambda s:_RANK.get(s,1))
        else:
            local_state="unknown"
        fed_state=_text(fed.get("state") or "unknown",32).lower()
        recovery=bool(fed.get("recovery_complete"))
        fresh=bool(fed.get("fresh"))
        current=fed_state=="current" and recovery and fresh
        if site==local and fed_state=="current" and recovery:
            current=True
        if current:
            state=local_state
            reason="authoritative_reconciliation_current"
        elif fed_state in {"recovering","reconciling"}:
            state="recovering";reason="federation_"+fed_state
        elif fed_state in {"partitioned","offline","failed","stale","degraded"}:
            state="stale";reason="federation_"+fed_state
        elif site==local and devices:
            state=local_state;reason="local_diagnostics"
        else:
            state="unknown";reason="remote_diagnostics_unavailable"
        row={
            "site_id":site,
            "label":_text(raw.get("label") or site,160),
            "state":state,
            "severity":_severity(state),
            "cause":next((x for d in devices for x in list(d.get("issues") or []) if x),reason if state!="healthy" else ""),
            "diagnostics_allowed":bool(fleet_site.get("diagnostics_allowed")) if fleet_site and site==local else False,
            "diagnostics_current":current and bool(devices),
            "federation_state":fed_state,
            "federation_recovery_complete":recovery,
            "device_count":len(devices),
            "healthy_device_count":sum(1 for d in devices if d.get("state")=="healthy"),
            "degraded_device_count":sum(1 for d in devices if d.get("state") in {"degraded","stale"}),
            "critical_device_count":sum(1 for d in devices if d.get("state") in {"failed","offline"}),
            "devices":devices,
            "agent_visible":visible,
            "current_claims_allowed":current and state=="healthy",
            "trust":{
                "diagnostics":"current" if current and devices else "qualified_last_known",
                "physical_claims":"section7_governed" if current else "do_not_claim_current",
                "reason":reason,
            },
        }
        row["message"]=(row["label"]+" fleet diagnostics are healthy and current.") if state=="healthy" else (
            row["label"]+" diagnostics are available, but federation recovery is not complete." if state=="recovering" else
            row["label"]+" diagnostics are last-known and must not be treated as current." if state=="stale" else
            row["label"]+" fleet diagnostics are offline." if state=="offline" else
            row["label"]+" has a critical fleet diagnostic failure." if state=="failed" else
            row["label"]+" has degraded fleet diagnostics." if state=="degraded" else
            row["label"]+" fleet health is unknown."
        )
        sites.append(row)
    visible=[s for s in sites if s["agent_visible"]]
    def worst(rows: list[dict[str,Any]]) -> str:
        return max((r["state"] for r in rows),key=lambda s:_RANK.get(s,1),default="unknown")
    issues=[{
        "site_id":s["site_id"],"label":s["label"],"state":s["state"],"severity":s["severity"],
        "cause":s["cause"],"message":s["message"],"diagnostics_current":s["diagnostics_current"],
        "current_claims_allowed":s["current_claims_allowed"],
    } for s in visible if s["state"]!="healthy"][:32]
    return {
        "protocol":FEDERATION_FLEET_HEALTH_PROTOCOL,"version":TRACKY_FEDERATION_FLEET_HEALTH_VERSION,"schema_version":1,
        "generated_at":now_ms,"local_site_id":local,"overall_state":worst(sites),
        "thresholds":{"stale_after_ms":stale_after_ms,"offline_after_ms":offline_after_ms},
        "counts":{"sites":len(sites),"devices":sum(s["device_count"] for s in sites),"healthy":sum(1 for s in sites if s["state"]=="healthy"),"degraded":sum(1 for s in sites if s["state"] in {"degraded","stale","recovering"}),"critical":sum(1 for s in sites if s["state"] in {"failed","offline"})},
        "sites":sites,
        "agent_context":{
            "overall_state":worst(visible),
            "sites":[{"site_id":s["site_id"],"label":s["label"],"state":s["state"],"severity":s["severity"],"cause":s["cause"],"diagnostics_current":s["diagnostics_current"],"current_claims_allowed":s["current_claims_allowed"]} for s in visible],
            "active_issues":issues,
            "summary":"; ".join((x["label"]+" is "+x["state"]) for x in issues[:6]) if issues else "All authorized fleet diagnostics are healthy and current.",
            "section7_health_is_authoritative":True,
            "diagnostics_never_promote_federation_freshness":True,
        },
        "privacy":{"diagnostic_content_included":False,"local_path_details_included":False,"network_endpoint_details_included":False,"secret_material_included":False,"conversations_included":False,"captured_media_content_included":False,"knowledge_content_included":False},
        "cloud_projection":{"summary_only":True,"read_only":True,"authority_mutation":False,"remote_command_execution":False},
        "boundaries":["section7-federation-health-remains-authoritative","diagnostics-never-promote-stale-state-to-current","diagnostics-are-observational-not-control-authority","cloud-mirror-is-read-only","privacy-safe-summary-only","agent-context-respects-federation-permissions"],
    }

def current_report() -> dict[str,Any]:
    return build_report(
        tracky_federation_operations.current_report(),
        tracky_federation_agent_health.current_report(),
        tracky_federation_access_operations.current_report(),
    )

def cloud_projection() -> dict[str,Any]:
    report=current_report()
    return {**report,"summary_only":True,"cloud_read_only":True,"authority_mutation":False,"remote_command_execution":False}

def public_capability() -> dict[str,Any]:
    return {
        "version":TRACKY_FEDERATION_FLEET_HEALTH_VERSION,"protocol":FEDERATION_FLEET_HEALTH_PROTOCOL,
        "states":list(_STATES),"section7_health_is_authoritative":True,
        "diagnostics_never_promote_federation_freshness":True,"permission_filtered_agent_context":True,
        "privacy_safe_summary_only":True,"cloud_read_only":True,"remote_command_execution":False,"authority_mutation":False,
    }

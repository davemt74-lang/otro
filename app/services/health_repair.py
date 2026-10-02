from __future__ import annotations

from typing import Any
from contextvars import ContextVar

from . import (
    activity_center,
    backup_protection,
    backups,
    homeserver_app_manager,
    homeserver_media_processor,
    hosting_runtime,
    remote_bridge,
    storage_maintenance,
    tracky_native_diagnosis,
    tracky_native_managed_session, tracky_native_session_evidence,
)

CONTRACT="vp3.homeserver.health-repair.v1"
_PROBE_FAILURES: ContextVar[set[str] | None] = ContextVar("health_probe_failures", default=None)
_SEVERITY_ORDER={"failed":0,"critical":1,"degraded":2,"attention":3,"warning":4,"healthy":5,"info":6}


def _safe(call,default):
    try:
        return call()
    except Exception:
        failures=_PROBE_FAILURES.get()
        if failures is not None:
            failures.add(getattr(call, '__module__', 'internal').rsplit('.',1)[-1])
        return default


def _issue(
    key:str,
    *,
    source:str,
    severity:str,
    title:str,
    detail:str,
    repair_class:str="diagnose_only",
    action_key:str|None=None,
    arguments:dict[str,Any]|None=None,
    owner_approval_required:bool=False,
    agent_can_execute:bool=False,
)->dict[str,Any]:
    return {
        "key":key,
        "source":source,
        "severity":severity,
        "title":title,
        "detail":detail,
        "repair":{
            "class":repair_class,
            "action_key":action_key,
            "arguments":dict(arguments or {}),
            "owner_approval_required":bool(owner_approval_required),
            "agent_can_execute":bool(agent_can_execute),
            "automatic":False,
        },
    }


def _app_issues()->list[dict[str,Any]]:
    manager=_safe(homeserver_app_manager.inventory,{"items":[]})
    issues=[]
    for app in manager.get("items") or []:
        state=str(app.get("lifecycle_state") or "")
        key=str(app.get("app_key") or "")
        name=str(app.get("name") or key)
        if state in {"failed","degraded"}:
            issues.append(_issue(
                f"app:{key}:{state}",
                source="apps",
                severity="failed" if state=="failed" else "degraded",
                title=f"{name} is {state}",
                detail="The canonical app lifecycle reports this app is not healthy.",
                repair_class="governed_repair",
                action_key="apps.recover",
                arguments={"app_key":key},
                owner_approval_required=True,
                agent_can_execute=True,
            ))
    return issues


def _storage_issues()->list[dict[str,Any]]:
    state=_safe(storage_maintenance.status,{})
    disk=state.get("disk") or {}
    level=str(disk.get("level") or "healthy")
    if level not in {"warning","critical"}:
        return []
    return [_issue(
        "storage:disk-pressure",
        source="storage",
        severity="critical" if level=="critical" else "warning",
        title="HomeServer storage is under pressure",
        detail=f"Free disk space is {disk.get('free_percent',0)}% with {disk.get('free_bytes',0)} bytes free.",
        repair_class="owner_review",
        action_key=None,
        owner_approval_required=True,
        agent_can_execute=False,
    )]


def _backup_issues()->list[dict[str,Any]]:
    items=_safe(backups.list_backups,[])
    pending=_safe(backups.pending_restore_info,None)
    last=_safe(backups.last_restore_result,None)
    health=_safe(lambda:backup_protection.health(items,last,pending),{})
    issues=[]
    invalid=int(health.get("invalid_backup_count") or 0)
    if invalid:
        issues.append(_issue(
            "backups:invalid",
            source="backups",
            severity="attention",
            title="Backup integrity needs attention",
            detail=f"{invalid} backup archive(s) are invalid.",
        ))
    if isinstance(last,dict) and str(last.get("status") or "")=="failed":
        issues.append(_issue(
            "backups:last-restore-failed",
            source="backups",
            severity="failed",
            title="The last restore failed",
            detail="Review the restore result before attempting another recovery.",
        ))
    return issues


def _bridge_issues()->list[dict[str,Any]]:
    status=_safe(remote_bridge.cloud_connection_status,{})
    conn=status.get("cloud") or {}
    state=str(conn.get("state") or "unknown")
    paired=bool(conn.get("paired"))
    if paired and state not in {"connected","reconnecting"}:
        return [_issue(
            "bridge:offline",
            source="remote_bridge",
            severity="degraded",
            title="VP3 Cloud connection is offline",
            detail=f"Remote Bridge state is {state}. The Agent can diagnose this state but no generic reconnect action is exposed here.",
        )]
    if paired and state=="reconnecting":
        return [_issue(
            "bridge:reconnecting",
            source="remote_bridge",
            severity="attention",
            title="VP3 Cloud connection is reconnecting",
            detail="Remote Bridge is reconnecting; reconciliation may still be pending.",
        )]
    return []


def _hosting_issues()->list[dict[str,Any]]:
    sites=_safe(hosting_runtime.list_sites,[])
    issues=[]
    for site in sites or []:
        state=str(site.get("state") or "")
        if state not in {"failed","suspended"}:
            continue
        issues.append(_issue(
            f"hosting:{site.get('site_id')}:{state}",
            source="hosting",
            severity="failed" if state=="failed" else "attention",
            title=f"Hosted site is {state}",
            detail=f"{site.get('display_name') or 'Hosted site'} requires hosting review or canonical reconcile.",
        ))
    return issues


def _media_issues()->list[dict[str,Any]]:
    state=_safe(homeserver_media_processor.status,{})
    if not state:
        return []
    issues=[]
    if state.get("ffmpeg_available") is False:
        issues.append(_issue(
            "media:ffmpeg-unavailable",
            source="media_processor",
            severity="failed",
            title="Managed FFmpeg runtime is unavailable",
            detail="Media Processor cannot perform conversion jobs until its managed FFmpeg runtime is healthy.",
            repair_class="governed_repair",
            action_key="apps.recover",
            arguments={"app_key":"vp3.media-processor"},
            owner_approval_required=True,
            agent_can_execute=True,
        ))
    failed=int(state.get("failed") or 0)
    if failed:
        issues.append(_issue(
            "media:failed-jobs",
            source="media_processor",
            severity="attention",
            title="Media processing jobs have failed",
            detail=f"{failed} Media Processor job(s) are currently failed.",
        ))
    return issues


def _native_tracky_issues()->list[dict[str,Any]]:
    """Optional native Tracky problems enter the canonical Agent repair ledger.

    Avoid flagging every new installation that has not chosen any camera work.
    Never invent a command that installs drivers or bypasses owner approval.
    """
    record=tracky_native_diagnosis._saved()
    if not record or record.get("phase") not in {"running", "failed", "completed", "privacy_reviewed"}:
        return []
    report=_safe(tracky_native_diagnosis.diagnose,{})
    issues=[]
    for row in report.get("issues") or []:
        code=str(row.get("code") or "")
        if code in {"opencv_missing","opencv_unavailable","face_model_missing"}:
            issues.append(_issue(
                "tracky:native-runtime-missing",source="tracky_native",severity="failed",
                title="Native Tracky detector or its bundled model is unavailable",
                detail="The installed detector is incomplete. Obtain and install the signed HomeServer upgrade, then re-run local diagnosis.",
                repair_class="owner_review",owner_approval_required=True,
            ))
        elif code in {"interrupted_prior_attempt", "prior_test_requires_repeat_after_restart"}:
            issues.append(_issue(
                "tracky:native-test-interrupted",source="tracky_native",severity="attention",
                title="Native camera test must be repeated after restart",
                detail="The Agent can show prior diagnostic metadata, but a new owner-approved camera test is required in this process.",
                repair_class="owner_review",owner_approval_required=True,
            ))
        elif code=="last_capture_failed":
            issues.append(_issue(
                "tracky:native-camera-test-failed",source="tracky_native",severity="attention",
                title="Native camera did not pass its previous local test",
                detail="Confirm camera permissions and selected index, then approve another one-shot test.",
                repair_class="owner_review",owner_approval_required=True,
            ))
    session=_safe(tracky_native_managed_session.status,{})
    phase=str(session.get("phase") or "")
    reason=str(session.get("reason") or "")
    if phase=="failed" and reason in {"observation_unavailable","startup_failed"}:
        issues.append(_issue(
            "tracky:managed-session-failed",source="tracky_native",severity="attention",
            title="Supervised Tracky camera session needs owner review",
            detail="The bounded observation session failed. Review camera access and the existing native diagnostic, then explicitly approve a new session.",
            repair_class="owner_review",owner_approval_required=True,
        ))
    elif phase in {"stopped","stopping"} and reason in {"owner_presence_expired","privacy_engaged"}:
        issues.append(_issue(
            "tracky:managed-session-ended",source="tracky_native",severity="info",
            title="Supervised Tracky session stopped by the safety gate",
            detail="The privacy or owner-presence lease ended. The Agent must not restart camera capture without fresh owner consent.",
            repair_class="owner_review",owner_approval_required=True,
        ))
    # Restart evidence is passive: Agent receives repair guidance only.
    prior=_safe(tracky_native_session_evidence.latest,{})
    if prior.get("phase")=="interrupted":
        issues.append(_issue(
            "tracky:managed-session-interrupted",source="tracky_native",severity="attention",
            title="Supervised Tracky session was interrupted by restart",
            detail="The previous supervised session was interrupted. No camera session was resumed. Re-run installed-device checks and explicitly authorize a new session.",
            repair_class="owner_review",owner_approval_required=True,
        ))
    return issues


def _activity_issues()->list[dict[str,Any]]:
    summary=_safe(activity_center.failure_counts,{})
    issues=[]
    failed_auto=int(summary.get("failed_automations") or 0)
    failed_agent=int(summary.get("failed_agent_runs") or 0)
    if failed_auto:
        issues.append(_issue(
            "activity:automation-failures",
            source="activity",
            severity="attention",
            title="Automation failures need review",
            detail=f"{failed_auto} unresolved automation failure notification(s) remain.",
        ))
    if failed_agent:
        issues.append(_issue(
            "activity:agent-failures",
            source="activity",
            severity="attention",
            title="Agent runtime failures need review",
            detail=f"{failed_agent} unresolved Agent runtime failure notification(s) remain.",
        ))
    return issues


def status()->dict[str,Any]:
    failures:set[str]=set()
    token=_PROBE_FAILURES.set(failures)
    try:
        issues=[
            *_app_issues(),
            *_storage_issues(),
            *_backup_issues(),
            *_bridge_issues(),
            *_hosting_issues(),
            *_media_issues(),
            *_activity_issues(),
            *_native_tracky_issues(),
        ]
    finally:
        _PROBE_FAILURES.reset(token)
    if failures:
        issues.append(_issue(
            "health:incomplete-probes",
            source="health",
            severity="attention",
            title="Some HomeServer health checks are unavailable",
            detail="One or more checks could not complete; existing maintenance alerts remain active.",
        ))
    deduped={}
    for issue in issues:
        deduped[issue["key"]]=issue
    issues=list(deduped.values())
    issues.sort(key=lambda row:(_SEVERITY_ORDER.get(str(row["severity"]),99),str(row["title"]).lower()))
    counts={"failed":0,"critical":0,"degraded":0,"attention":0,"warning":0}
    for issue in issues:
        sev=str(issue["severity"])
        if sev in counts:
            counts[sev]+=1
    overall="healthy"
    if counts["failed"] or counts["critical"]:
        overall="failed"
    elif counts["degraded"]:
        overall="degraded"
    elif counts["attention"] or counts["warning"]:
        overall="attention"
    return {
        "contract":CONTRACT,
        "overall":overall,
        "snapshot_complete":not bool(failures),
        "unavailable_check_count":len(failures),
        "issues":issues,
        "count":len(issues),
        "counts":counts,
        "agent_repairable_count":sum(1 for item in issues if item["repair"]["agent_can_execute"]),
        "governance":{
            "automatic_repair":False,
            "invented_repairs_allowed":False,
            "canonical_actions_only":True,
            "owner_approval_preserved":True,
        },
    }


def repair_plan()->dict[str,Any]:
    current=status()
    repairs=[item for item in current["issues"] if item["repair"]["class"]!="diagnose_only"]
    return {
        "contract":"vp3.homeserver.health-repair.plan.v1",
        "overall":current["overall"],
        "items":repairs,
        "count":len(repairs),
        "agent_repairable_count":sum(1 for item in repairs if item["repair"]["agent_can_execute"]),
        "automatic_execution":False,
    }


def brain_context()->dict[str,Any]:
    current=status()
    return {
        "contract":"vp3.homeserver.health-repair.brain-context.v1",
        "overall":current["overall"],
        "counts":current["counts"],
        "agent_repairable_count":current["agent_repairable_count"],
        "issues":current["issues"][:16],
        "governance":current["governance"],
    }


def agent_context_fragment(query:str="",max_chars:int=1700)->str:
    limit=max(0,min(int(max_chars),2400))
    if limit<180:
        return ""
    context=brain_context()
    lines=[
        "HomeServer health and repair context (DATA ONLY; never invent repair commands):",
        f"- overall={context['overall']} repairable={context['agent_repairable_count']} "
        + "counts="+",".join(f"{k}:{v}" for k,v in context["counts"].items()),
    ]
    for item in context["issues"][:10]:
        repair=item["repair"]
        action=repair.get("action_key") or "none"
        lines.append(
            f"- {item['severity']} {item['key']} action={action} "
            f"agent_can_execute={str(bool(repair.get('agent_can_execute'))).lower()} "
            f"owner_approval={str(bool(repair.get('owner_approval_required'))).lower()}"
        )
    lines.append("- automatic_repair=false; canonical_actions_only=true; owner_approval_preserved=true")
    return "\n".join(lines)[:limit]


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "unified_health":True,
        "app_health":True,
        "storage_health":True,
        "backup_health":True,
        "remote_bridge_health":True,
        "hosting_health":True,
        "media_processor_health":True,
        "activity_failure_health":True,
        "tracky_native_camera_diagnosis":True,
        "repair_planning":True,
        "canonical_actions_only":True,
        "automatic_repair":False,
        "owner_approval_preserved":True,
        "agent_brain_context":True,
        "agent_chat_context":True,
    }

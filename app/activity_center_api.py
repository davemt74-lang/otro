from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .services import activity_center, health_maintenance

router=APIRouter()


class NotificationUpdate(BaseModel):
    read:bool|None=None
    dismissed:bool|None=None
    archived:bool|None=None


class PreferenceUpdate(BaseModel):
    enabled:bool|None=None
    minimum_level:str|None=Field(default=None,pattern="^(info|success|warning|error|action_required)$")
    sound_enabled:bool|None=None
    toast_enabled:bool|None=None


def _call(fn,*args,**kwargs):
    try:
        return fn(*args,**kwargs)
    except activity_center.ActivityCenterError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/api/v1/control/activity-center/capability")
def capability()->dict:
    return activity_center.public_capability()


@router.get("/api/v1/control/activity-center/summary")
def summary()->dict:
    return _call(activity_center.summary)


@router.get("/api/v1/control/activity-center/brain-context")
def brain_context(limit:int=Query(default=20,ge=1,le=50))->dict:
    return _call(activity_center.brain_context,limit)


@router.post("/api/v1/control/activity-center/sync")
def sync()->dict:
    # Explicit owner refresh scans health before projecting Activity Center events.
    health=_call(health_maintenance.sync_health_notifications)
    events=_call(activity_center.sync_notifications)
    return {**events,"health":health}


@router.patch("/api/v1/control/activity-center/notifications/{notification_id}")
def notification_update(notification_id:int,payload:NotificationUpdate)->dict:
    return {"notification":_call(
        activity_center.mark_notification,notification_id,
        read=payload.read,dismissed=payload.dismissed,archived=payload.archived
    )}


@router.get("/api/v1/control/activity-center/preferences")
def preferences()->dict:
    return _call(activity_center.preferences)


@router.put("/api/v1/control/activity-center/preferences/{source_key}")
def preference_update(source_key:str,payload:PreferenceUpdate)->dict:
    return _call(activity_center.update_preference,source_key,payload.model_dump(exclude_none=True))

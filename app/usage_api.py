from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .main import current_app
from .services import usage

router = APIRouter()


class CloudUsageEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=160)
    provider_key: str = Field(default="vp3-cloud", max_length=80)
    model: str = Field(default="", max_length=200)
    request_kind: str = Field(default="chat", max_length=80)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    billable_tokens: int = Field(default=0, ge=0)
    balance_after_tokens: int | None = Field(default=None, ge=0)


def _require_usage(permission: str):
    def dependency(identity: dict = Depends(current_app)) -> dict:
        if permission not in identity["permissions"]:
            raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
        return identity
    return dependency


@router.get("/api/v1/usage")
def client_usage(
    limit: int = Query(default=100, ge=1, le=500),
    identity: dict = Depends(_require_usage("usage.read")),
) -> dict:
    source = f"app:{identity['app_key']}"
    items = usage.list_usage(limit=limit, source_app_key=source)
    return {
        "items": items,
        "summary": usage.usage_summary(source_app_key=source),
        "app": identity["app_key"],
    }


@router.post("/api/v1/usage/cloud")
def client_cloud_usage(
    payload: CloudUsageEvent,
    identity: dict = Depends(_require_usage("usage.write")),
) -> dict:
    try:
        event = usage.record_usage(
            event_id=payload.event_id,
            source_app_key=f"app:{identity['app_key']}",
            compute_source="vp3_cloud",
            provider_key=payload.provider_key,
            model=payload.model,
            request_kind=payload.request_kind,
            prompt_tokens=payload.prompt_tokens,
            completion_tokens=payload.completion_tokens,
            total_tokens=payload.total_tokens,
            billable_tokens=payload.billable_tokens,
            balance_after_tokens=payload.balance_after_tokens,
        )
    except usage.UsageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"recorded": True, "event": event}


@router.get("/api/v1/control/usage")
def control_usage(
    limit: int = Query(default=250, ge=1, le=1000),
    source: str | None = Query(default=None, max_length=40),
) -> dict:
    try:
        items = usage.list_usage(limit=limit, compute_source=source or None)
    except usage.UsageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items, "summary": usage.usage_summary()}

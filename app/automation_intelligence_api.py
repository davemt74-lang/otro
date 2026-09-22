from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .services import automation_intelligence

router = APIRouter()


class IntelligenceSettingsUpdate(BaseModel):
    enabled: bool = True
    scan_interval_seconds: int = Field(default=3600, ge=300, le=86400)
    lookback_days: int = Field(default=21, ge=7, le=90)
    min_occurrences: int = Field(default=4, ge=3, le=20)
    time_bucket_minutes: int = Field(default=30)
    max_proposals_per_scan: int = Field(default=12, ge=1, le=50)
    suppression_days: int = Field(default=30, ge=1, le=365)


class ProposalDismiss(BaseModel):
    note: str = Field(default="", max_length=1000)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except automation_intelligence.AutomationIntelligenceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc


@router.get("/api/v1/control/vp3-os/automation/intelligence")
def owner_intelligence_overview() -> dict:
    return _call(automation_intelligence.overview)


@router.put("/api/v1/control/vp3-os/automation/intelligence/settings")
def owner_intelligence_settings(
    payload: IntelligenceSettingsUpdate,
) -> dict:
    return {
        "settings": _call(
            automation_intelligence.update_settings,
            enabled=payload.enabled,
            scan_interval_seconds=payload.scan_interval_seconds,
            lookback_days=payload.lookback_days,
            min_occurrences=payload.min_occurrences,
            time_bucket_minutes=payload.time_bucket_minutes,
            max_proposals_per_scan=payload.max_proposals_per_scan,
            suppression_days=payload.suppression_days,
        )
    }


@router.post("/api/v1/control/vp3-os/automation/intelligence/scan")
def owner_intelligence_scan() -> dict:
    return _call(automation_intelligence.scan_patterns)


@router.get("/api/v1/control/vp3-os/automation/intelligence/proposals")
def owner_intelligence_proposals(
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {
        "items": _call(
            automation_intelligence.list_proposals,
            status,
            limit,
        )
    }


@router.post(
    "/api/v1/control/vp3-os/automation/intelligence/"
    "proposals/{proposal_id}/simulate"
)
def owner_intelligence_simulate(proposal_id: int) -> dict:
    return _call(
        automation_intelligence.simulate_proposal,
        proposal_id,
    )


@router.post(
    "/api/v1/control/vp3-os/automation/intelligence/"
    "proposals/{proposal_id}/materialize"
)
def owner_intelligence_materialize(proposal_id: int) -> dict:
    return {
        "proposal": _call(
            automation_intelligence.materialize_proposal,
            proposal_id,
        )
    }


@router.post(
    "/api/v1/control/vp3-os/automation/intelligence/"
    "proposals/{proposal_id}/enable"
)
def owner_intelligence_enable(proposal_id: int) -> dict:
    return {
        "proposal": _call(
            automation_intelligence.enable_materialized_proposal,
            proposal_id,
        )
    }


@router.post(
    "/api/v1/control/vp3-os/automation/intelligence/"
    "proposals/{proposal_id}/dismiss"
)
def owner_intelligence_dismiss(
    proposal_id: int,
    payload: ProposalDismiss,
) -> dict:
    return {
        "proposal": _call(
            automation_intelligence.dismiss_proposal,
            proposal_id,
            note=payload.note,
        )
    }

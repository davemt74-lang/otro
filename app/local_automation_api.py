from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .services import local_automation

router = APIRouter()


class RoutineUpsert(BaseModel):
    routine_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    approval_mode: str = Field(default="ask_every_time", max_length=40)
    steps: list[dict[str, Any]] = Field(default_factory=list, min_length=1, max_length=16)


class RuleUpsert(BaseModel):
    rule_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    routine_key: str = Field(min_length=1, max_length=80)
    trigger_kind: str = Field(min_length=1, max_length=40)
    trigger: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)


class RuntimeSettingsUpdate(BaseModel):
    enabled: bool = True
    poll_seconds: int = Field(default=15, ge=5, le=300)
    max_actions_per_run: int = Field(default=12, ge=1, le=16)
    max_rule_fires_per_minute: int = Field(default=20, ge=1, le=60)


class EnabledUpdate(BaseModel):
    enabled: bool


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except local_automation.LocalAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/control/vp3-os/automation/rules-runtime")
def owner_rules_overview() -> dict:
    return {
        "version": local_automation.AUTOMATION_RULES_VERSION,
        "settings": _call(local_automation.get_settings),
        "routines": _call(local_automation.list_routines),
        "rules": _call(local_automation.list_rules),
        "recent_executions": _call(local_automation.list_executions, 100),
        "governance": {
            "direct_physical_execution": False,
            "device_commands_require_local_owner_approval": True,
            "approval_modes": ["suggest_only", "ask_every_time"],
        },
    }


@router.put("/api/v1/control/vp3-os/automation/routines/{routine_key}")
def owner_routine_upsert(routine_key: str, payload: RoutineUpsert) -> dict:
    if routine_key.strip().lower() != payload.routine_key.strip().lower():
        raise HTTPException(status_code=422, detail="routine_key path and payload must match")
    return {"routine": _call(
        local_automation.upsert_routine,
        payload.routine_key,
        payload.name,
        description=payload.description,
        enabled=payload.enabled,
        approval_mode=payload.approval_mode,
        steps=payload.steps,
    )}


@router.post("/api/v1/control/vp3-os/automation/routines/{routine_key}/run")
def owner_routine_run(routine_key: str) -> dict:
    return _call(local_automation.run_routine, routine_key, source_kind="owner:manual-routine")


@router.put("/api/v1/control/vp3-os/automation/routines/{routine_key}/enabled")
def owner_routine_enabled(routine_key: str, payload: EnabledUpdate) -> dict:
    return {
        "routine": _call(
            local_automation.set_routine_enabled,
            routine_key,
            payload.enabled,
        )
    }


@router.put("/api/v1/control/vp3-os/automation/rules/{rule_key}")
def owner_rule_upsert(rule_key: str, payload: RuleUpsert) -> dict:
    if rule_key.strip().lower() != payload.rule_key.strip().lower():
        raise HTTPException(status_code=422, detail="rule_key path and payload must match")
    return {"rule": _call(
        local_automation.upsert_rule,
        payload.rule_key,
        payload.name,
        routine_key=payload.routine_key,
        trigger_kind=payload.trigger_kind,
        trigger=payload.trigger,
        conditions=payload.conditions,
        description=payload.description,
        enabled=payload.enabled,
        cooldown_seconds=payload.cooldown_seconds,
    )}


@router.post("/api/v1/control/vp3-os/automation/rules/{rule_key}/run")
def owner_rule_run(rule_key: str) -> dict:
    return _call(local_automation.evaluate_rule, rule_key, force_manual=True)


@router.put("/api/v1/control/vp3-os/automation/rules/{rule_key}/enabled")
def owner_rule_enabled(rule_key: str, payload: EnabledUpdate) -> dict:
    return {
        "rule": _call(
            local_automation.set_rule_enabled,
            rule_key,
            payload.enabled,
        )
    }


@router.put("/api/v1/control/vp3-os/automation/rules-runtime/settings")
def owner_rules_settings(payload: RuntimeSettingsUpdate) -> dict:
    return {"settings": _call(
        local_automation.update_settings,
        enabled=payload.enabled,
        poll_seconds=payload.poll_seconds,
        max_actions_per_run=payload.max_actions_per_run,
        max_rule_fires_per_minute=payload.max_rule_fires_per_minute,
    )}


@router.get("/api/v1/control/vp3-os/automation/rules-runtime/executions")
def owner_rule_executions(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": _call(local_automation.list_executions, limit)}

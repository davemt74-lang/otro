from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import app_scopes, cognitive_runtime, plugins
from .services.pairing import authenticate


@asynccontextmanager
async def cognition_lifespan(_):
    cognitive_runtime.scheduler.start()
    try:
        yield
    finally:
        cognitive_runtime.scheduler.stop()


router = APIRouter(lifespan=cognition_lifespan)


class CognitiveEventCreate(BaseModel):
    event_id: str | None = Field(default=None, max_length=160)
    event_type: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    plugin_key: str | None = Field(default=None, max_length=80)
    entity_type: str | None = Field(default=None, max_length=160)
    entity_key: str | None = Field(default=None, max_length=240)
    correlation_id: str | None = Field(default=None, max_length=160)
    conversation_id: str | None = Field(default=None, max_length=64)
    importance: float = Field(default=0.5, ge=0, le=1)
    privacy_scope: str = Field(default="private", max_length=20)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = Field(default=None, max_length=80)
    memory_candidate: bool = False
    memory_type: str | None = Field(default=None, max_length=40)
    memory_key: str | None = Field(default=None, max_length=160)


class AwarenessStatusUpdate(BaseModel):
    status: str = Field(min_length=4, max_length=20)


class MemoryCandidateDecision(BaseModel):
    decision: str = Field(min_length=7, max_length=20)


class PluginRegister(BaseModel):
    manifest: dict[str, Any]
    trusted: bool = False


class PluginStatusUpdate(BaseModel):
    status: str = Field(min_length=6, max_length=20)


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _require(permission: str):
    def dependency(identity: dict = Depends(_current_app)) -> dict:
        if permission not in identity["permissions"]:
            raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
        return identity
    return dependency


def _raise_cognitive(exc: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(exc, "status_code", 422), detail=str(exc))


def _app_source(identity: dict) -> str:
    return f"app:{identity['app_key']}"


@router.post("/api/v1/events")
def app_emit_event(
    payload: CognitiveEventCreate,
    identity: dict = Depends(_require("events.write")),
) -> dict:
    permissions = set(identity["permissions"])
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    if payload.plugin_key and not app_scopes.plugin_allowed(scope, payload.plugin_key):
        raise HTTPException(status_code=403, detail="Plugin is outside this application's allowed scope")
    if payload.memory_candidate and not app_scopes.memory_key_allowed(scope, payload.memory_key):
        raise HTTPException(status_code=403, detail="Memory candidate key is outside this application's allowed scope")
    try:
        return cognitive_runtime.emit_event(
            source_app_key=_app_source(identity),
            source_kind="app",
            event_id=payload.event_id,
            event_type=payload.event_type,
            summary=payload.summary,
            plugin_key=payload.plugin_key,
            entity_type=payload.entity_type,
            entity_key=payload.entity_key,
            correlation_id=payload.correlation_id,
            conversation_id=payload.conversation_id,
            importance=payload.importance,
            privacy_scope=payload.privacy_scope,
            payload=payload.payload,
            occurred_at=payload.occurred_at,
            memory_candidate=payload.memory_candidate,
            memory_type=payload.memory_type,
            memory_key=payload.memory_key,
            allow_memory_candidate="memory.write" in permissions,
        )
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.get("/api/v1/events")
def app_events(
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None, max_length=160),
    identity: dict = Depends(_require("events.read")),
) -> dict:
    return {
        "items": cognitive_runtime.list_events(
            limit=limit,
            source_app_key=_app_source(identity),
            event_type=event_type,
            include_payload=True,
        ),
        "app": identity["app_key"],
    }


@router.get("/api/v1/awareness")
def app_awareness(
    limit: int = Query(default=50, ge=1, le=200),
    identity: dict = Depends(_require("awareness.read")),
) -> dict:
    return {"items": cognitive_runtime.list_awareness(limit=limit, status="open"), "app": identity["app_key"]}


@router.get("/api/v1/plugins")
def app_plugins(identity: dict = Depends(_require("plugins.read"))) -> dict:
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    items = [item for item in plugins.list_plugins(active_only=True) if app_scopes.plugin_allowed(scope, item.get("plugin_key") or item.get("key"))]
    return {"items": items, "app": identity["app_key"]}


@router.get("/api/v1/control/cognition")
def control_cognition() -> dict:
    return {
        "runtime": {**cognitive_runtime.overview(), "scheduler_running": cognitive_runtime.scheduler.running},
        "awareness": cognitive_runtime.list_awareness(limit=20, status="open"),
        "memory_candidates": cognitive_runtime.list_memory_candidates(limit=20, status="pending"),
        "plugins": plugins.list_plugins(),
    }


@router.get("/api/v1/control/cognition/events")
def control_events(
    limit: int = Query(default=200, ge=1, le=500),
    source_app_key: str | None = Query(default=None, max_length=160),
    event_type: str | None = Query(default=None, max_length=160),
    include_payload: bool = Query(default=False),
) -> dict:
    return {
        "items": cognitive_runtime.list_events(
            limit=limit,
            source_app_key=source_app_key,
            event_type=event_type,
            include_payload=include_payload,
        )
    }


@router.post("/api/v1/control/cognition/events")
def control_emit_event(payload: CognitiveEventCreate) -> dict:
    try:
        return cognitive_runtime.emit_event(
            source_app_key="owner",
            source_kind="owner",
            event_id=payload.event_id,
            event_type=payload.event_type,
            summary=payload.summary,
            plugin_key=payload.plugin_key,
            entity_type=payload.entity_type,
            entity_key=payload.entity_key,
            correlation_id=payload.correlation_id,
            conversation_id=payload.conversation_id,
            importance=payload.importance,
            privacy_scope=payload.privacy_scope,
            payload=payload.payload,
            occurred_at=payload.occurred_at,
            memory_candidate=payload.memory_candidate,
            memory_type=payload.memory_type,
            memory_key=payload.memory_key,
            allow_memory_candidate=True,
        )
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.post("/api/v1/control/cognition/process")
def control_process_cognition() -> dict:
    try:
        return cognitive_runtime.tick()
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.get("/api/v1/control/cognition/awareness")
def control_awareness(
    limit: int = Query(default=200, ge=1, le=500),
    status: str | None = Query(default="open", max_length=20),
) -> dict:
    try:
        return {"items": cognitive_runtime.list_awareness(limit=limit, status=status)}
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.patch("/api/v1/control/cognition/awareness/{awareness_id}")
def control_awareness_status(awareness_id: int, payload: AwarenessStatusUpdate) -> dict:
    try:
        return {"awareness": cognitive_runtime.update_awareness_status(awareness_id, payload.status)}
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.get("/api/v1/control/cognition/memory-candidates")
def control_memory_candidates(
    limit: int = Query(default=200, ge=1, le=500),
    status: str | None = Query(default="pending", max_length=20),
) -> dict:
    try:
        return {"items": cognitive_runtime.list_memory_candidates(limit=limit, status=status)}
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.post("/api/v1/control/cognition/memory-candidates/{candidate_id}")
def control_memory_candidate_decision(candidate_id: int, payload: MemoryCandidateDecision) -> dict:
    try:
        return {"memory_candidate": cognitive_runtime.decide_memory_candidate(candidate_id, payload.decision)}
    except cognitive_runtime.CognitiveError as exc:
        raise _raise_cognitive(exc) from exc


@router.get("/api/v1/control/plugins")
def control_plugins() -> dict:
    return {"items": plugins.list_plugins()}


@router.post("/api/v1/control/plugins")
def control_register_plugin(payload: PluginRegister) -> dict:
    try:
        return {"plugin": plugins.register_plugin(payload.manifest, trusted=payload.trusted)}
    except plugins.PluginError as exc:
        raise _raise_cognitive(exc) from exc


@router.patch("/api/v1/control/plugins/{plugin_key}")
def control_plugin_status(plugin_key: str, payload: PluginStatusUpdate) -> dict:
    try:
        return {"plugin": plugins.set_plugin_status(plugin_key, payload.status)}
    except plugins.PluginError as exc:
        raise _raise_cognitive(exc) from exc

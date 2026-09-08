from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .brain_api import ChatRequest, client_chat
from .services import brain, delegation
from .services.pairing import authenticate

router = APIRouter()


class DelegatedAgent(BaseModel):
    name: str = Field(min_length=1, max_length=190)
    role: str = Field(default="", max_length=80)
    instructions: str = Field(default="", max_length=4000)


class DelegatedMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class DelegationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: str | None = Field(default=None, max_length=64)
    external_conversation_id: str | None = Field(default=None, max_length=160)
    delegation: DelegatedAgent | None = None
    history: list[DelegatedMessage] = Field(default_factory=list, max_length=12)
    surface_context: dict[str, Any] = Field(default_factory=dict)
    include_memory: bool | None = None
    include_knowledge: bool | None = None
    include_contacts: bool | None = None
    cloud_allowed: bool | None = None
    max_context_chars: int | None = Field(default=None, ge=2000, le=24000)


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _require_chat(identity: dict = Depends(_current_app)) -> dict:
    if "agent.chat" not in identity["permissions"]:
        raise HTTPException(status_code=403, detail="Permission required: agent.chat")
    return identity


def _delegate(payload: DelegationRequest, identity: dict) -> dict:
    if payload.delegation is None:
        raise HTTPException(status_code=422, detail="Delegation metadata is required.")
    external_id = str(payload.external_conversation_id or "").strip()
    if not external_id:
        raise HTTPException(status_code=422, detail="external_conversation_id is required for delegated Agent Chat.")
    permissions = set(identity["permissions"])
    try:
        return delegation.chat(
            f"app:{identity['app_key']}",
            payload.message,
            external_id,
            payload.delegation.model_dump(),
            [item.model_dump() for item in payload.history],
            payload.surface_context,
            include_memory=(payload.include_memory is not False) and "memory.read" in permissions,
            include_knowledge=(payload.include_knowledge is not False) and "knowledge.search" in permissions,
            include_contacts=(payload.include_contacts is not False) and "contacts.read" in permissions,
            cloud_allowed=payload.cloud_allowed is not False,
            max_context_chars=payload.max_context_chars or 12000,
            tool_permissions=permissions,
        )
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/chat")
def compatible_chat(payload: DelegationRequest, identity: dict = Depends(_require_chat)) -> dict:
    if payload.delegation is not None:
        return _delegate(payload, identity)

    # v0.18-compatible payloads continue through the canonical stateful
    # HomeServer chat implementation unchanged.
    legacy = ChatRequest(
        message=payload.message,
        conversation_id=payload.conversation_id,
        include_memory=payload.include_memory,
        include_knowledge=payload.include_knowledge,
        include_contacts=payload.include_contacts,
        cloud_allowed=payload.cloud_allowed,
        max_context_chars=payload.max_context_chars,
    )
    return client_chat(legacy, identity)

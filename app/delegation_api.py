from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

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
    external_conversation_id: str = Field(min_length=1, max_length=160)
    agent: DelegatedAgent
    history: list[DelegatedMessage] = Field(default_factory=list, max_length=12)
    surface_context: dict[str, Any] = Field(default_factory=dict)
    include_memory: bool = True
    include_knowledge: bool = True
    include_contacts: bool = True
    cloud_allowed: bool = True
    max_context_chars: int = Field(default=12000, ge=2000, le=24000)


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


@router.post("/api/v1/delegation/chat")
def delegated_chat(payload: DelegationRequest, identity: dict = Depends(_require_chat)) -> dict:
    permissions = set(identity["permissions"])
    try:
        return delegation.chat(
            f"app:{identity['app_key']}",
            payload.message,
            payload.external_conversation_id,
            payload.agent.model_dump(),
            [item.model_dump() for item in payload.history],
            payload.surface_context,
            include_memory=payload.include_memory and "memory.read" in permissions,
            include_knowledge=payload.include_knowledge and "knowledge.search" in permissions,
            include_contacts=payload.include_contacts and "contacts.read" in permissions,
            cloud_allowed=payload.cloud_allowed,
            max_context_chars=payload.max_context_chars,
            tool_permissions=permissions,
        )
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

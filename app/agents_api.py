from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .services import agent_management

router = APIRouter(prefix="/api/v1/control/agents", tags=["agents"])


class ManagedAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=12000)
    model: str = Field(default="", max_length=120)


class ManagedVoiceProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: str | None = Field(default=None, max_length=120)
    speaking_rate: float | None = Field(default=None, ge=0.6, le=1.6)
    sentence_silence: float | None = Field(default=None, ge=0.0, le=1.5)


class ManagedPersonaRequest(ManagedAgentRequest):
    voice_profile: ManagedVoiceProfileRequest = Field(default_factory=ManagedVoiceProfileRequest)


def _raise(exc: agent_management.AgentManagementError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("")
def list_managed_agents() -> dict:
    return agent_management.list_agents()


@router.post("")
def create_managed_agent(payload: ManagedAgentRequest) -> dict:
    try:
        return agent_management.create_agent(payload.model_dump())
    except agent_management.AgentManagementError as exc:
        _raise(exc)


@router.get("/{agent_id}")
def get_managed_agent(agent_id: int) -> dict:
    try:
        return agent_management.get_agent(agent_id)
    except agent_management.AgentManagementError as exc:
        _raise(exc)


@router.put("/{agent_id}")
def update_managed_agent(agent_id: int, payload: ManagedAgentRequest) -> dict:
    try:
        return agent_management.update_agent(agent_id, payload.model_dump())
    except agent_management.AgentManagementError as exc:
        _raise(exc)


@router.put("/{agent_id}/persona")
def save_managed_agent_persona(agent_id: int, payload: ManagedPersonaRequest) -> dict:
    try:
        return agent_management.save_persona(agent_id, payload.model_dump())
    except agent_management.AgentManagementError as exc:
        _raise(exc)


@router.delete("/{agent_id}")
def delete_managed_agent(agent_id: int) -> dict:
    try:
        return agent_management.delete_agent(agent_id)
    except agent_management.AgentManagementError as exc:
        _raise(exc)


@router.post("/{agent_id}/duplicate")
def duplicate_managed_agent(agent_id: int) -> dict:
    try:
        return agent_management.duplicate_agent(agent_id)
    except agent_management.AgentManagementError as exc:
        _raise(exc)

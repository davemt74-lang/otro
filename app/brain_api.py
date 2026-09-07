from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import agent_tools, brain, providers
from .services.pairing import authenticate

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: str | None = Field(default=None, max_length=64)


class ProviderUpdate(BaseModel):
    base_url: str = Field(min_length=8, max_length=300)
    model: str = Field(default="", max_length=160)
    enabled: bool = False


class AgentToolPolicyUpdate(BaseModel):
    enabled: bool = False
    max_calls: int = Field(default=3, ge=1, le=3)
    allow_write_proposals: bool = False


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


def _app_source(identity: dict) -> str:
    return f"app:{identity['app_key']}"


def _chat_or_http(
    source: str,
    payload: ChatRequest,
    *,
    include_memory: bool = True,
    include_knowledge: bool = True,
    tool_permissions: set[str] | None = None,
    owner_tools: bool = False,
) -> dict:
    try:
        return brain.chat(
            source,
            payload.message,
            payload.conversation_id,
            include_memory=include_memory,
            include_knowledge=include_knowledge,
            tool_permissions=tool_permissions,
            owner_tools=owner_tools,
        )
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/chat")
def client_chat(payload: ChatRequest, identity: dict = Depends(_require_chat)) -> dict:
    permissions = set(identity["permissions"])
    return _chat_or_http(
        _app_source(identity),
        payload,
        include_memory="memory.read" in permissions,
        include_knowledge="knowledge.search" in permissions,
        tool_permissions=permissions,
        owner_tools=False,
    )


@router.get("/api/v1/conversations")
def client_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    identity: dict = Depends(_require_chat),
) -> dict:
    return {"items": brain.list_conversations(_app_source(identity), limit=limit)}


@router.get("/api/v1/conversations/{conversation_id}")
def client_conversation(conversation_id: str, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return brain.get_conversation(_app_source(identity), conversation_id)
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/control/chat")
def control_chat(payload: ChatRequest) -> dict:
    return _chat_or_http("owner", payload, tool_permissions=set(), owner_tools=True)


@router.get("/api/v1/control/conversations")
def control_conversations(limit: int = Query(default=50, ge=1, le=100)) -> dict:
    return {"items": brain.list_conversations("owner", limit=limit)}


@router.get("/api/v1/control/conversations/{conversation_id}")
def control_conversation(conversation_id: str) -> dict:
    try:
        return brain.get_conversation("owner", conversation_id)
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/api/v1/control/conversations/{conversation_id}")
def control_conversation_delete(conversation_id: str) -> dict:
    if not brain.delete_conversation("owner", conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True}


@router.get("/api/v1/control/provider")
def control_provider() -> dict:
    try:
        return {"provider": providers.get_ollama()}
    except providers.ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/api/v1/control/provider")
def control_provider_update(payload: ProviderUpdate) -> dict:
    try:
        provider = providers.save_ollama(payload.base_url, payload.model, payload.enabled)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"provider": provider}


@router.post("/api/v1/control/provider/test")
def control_provider_test(payload: ProviderUpdate) -> dict:
    try:
        return providers.discover_ollama_models(payload.base_url)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/v1/control/agent-tools")
def control_agent_tools() -> dict:
    policy = agent_tools.get_policy()
    return {
        "policy": policy,
        "mode": "approval_gated",
        "available_tools": [
            schema["function"]["name"]
            for schema in agent_tools.model_tool_schemas(
                owner=True,
                allow_write_proposals=policy["allow_write_proposals"],
            )
        ],
    }


@router.put("/api/v1/control/agent-tools")
def control_agent_tools_update(payload: AgentToolPolicyUpdate) -> dict:
    try:
        policy = agent_tools.save_policy(
            payload.enabled,
            payload.max_calls,
            payload.allow_write_proposals,
        )
    except agent_tools.AgentToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"policy": policy, "mode": "approval_gated"}

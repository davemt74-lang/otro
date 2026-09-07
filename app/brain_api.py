from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import agent_tools, brain, context_chat, context_engine, provider_secrets, providers
from .services.pairing import authenticate

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: str | None = Field(default=None, max_length=64)
    include_memory: bool | None = None
    include_knowledge: bool | None = None
    include_contacts: bool | None = None
    cloud_allowed: bool | None = None
    max_context_chars: int | None = Field(default=None, ge=2000, le=24000)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ContextSettingsUpdate(BaseModel):
    include_memory: bool = True
    include_knowledge: bool = True
    include_contacts: bool = True
    cloud_allowed: bool = False
    max_context_chars: int = Field(default=12000, ge=2000, le=24000)


class ProviderUpdate(BaseModel):
    base_url: str = Field(min_length=8, max_length=300)
    model: str = Field(default="", max_length=160)
    enabled: bool = False


class ExternalProviderUpdate(BaseModel):
    provider_key: str = Field(min_length=3, max_length=40)
    model: str = Field(default="", max_length=200)
    enabled: bool = False


class InferencePreferenceUpdate(BaseModel):
    preferred_provider: str = Field(default="auto", min_length=3, max_length=40)


class ProviderCredentialUpdate(BaseModel):
    anthropic: str | None = Field(default=None, max_length=4000)
    openai: str | None = Field(default=None, max_length=4000)
    openrouter: str | None = Field(default=None, max_length=4000)
    elevenlabs: str | None = Field(default=None, max_length=4000)
    clear: list[str] = Field(default_factory=list, max_length=4)


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


def _safe_inference_status() -> dict:
    status = providers.inference_status()
    return {
        "available": bool(status["available"]),
        "selected_provider": status["selected_provider"],
        "model": status["model"],
        "compute_source": status["compute_source"],
        "cloud_fallback_required": bool(status["cloud_fallback_required"]),
    }


def _context_options(payload: ChatRequest) -> dict:
    return {
        "include_memory": payload.include_memory,
        "include_knowledge": payload.include_knowledge,
        "include_contacts": payload.include_contacts,
        "cloud_allowed": payload.cloud_allowed,
        "max_context_chars": payload.max_context_chars,
    }


def _conversation_payload(source: str, conversation_id: str) -> dict:
    try:
        result = brain.get_conversation(source, conversation_id)
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    result["context_settings"] = context_engine.ensure_settings(conversation_id)
    result["context_history"] = context_engine.recent_sources(conversation_id, limit=5)
    return result


def _chat_or_http(
    source: str,
    payload: ChatRequest,
    *,
    include_memory: bool = True,
    include_knowledge: bool = True,
    include_contacts: bool = False,
    tool_permissions: set[str] | None = None,
    owner_tools: bool = False,
) -> dict:
    try:
        return context_chat.chat(
            source,
            payload.message,
            payload.conversation_id,
            include_memory=include_memory,
            include_knowledge=include_knowledge,
            include_contacts=include_contacts,
            context_options=_context_options(payload),
            tool_permissions=tool_permissions,
            owner_tools=owner_tools,
        )
    except (brain.BrainError, context_engine.ContextError) as exc:
        status_code = getattr(exc, "status_code", 422)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/api/v1/inference/status")
def client_inference_status(identity: dict = Depends(_require_chat)) -> dict:
    return {**_safe_inference_status(), "app": identity["app_key"]}


@router.post("/api/v1/chat")
def client_chat(payload: ChatRequest, identity: dict = Depends(_require_chat)) -> dict:
    permissions = set(identity["permissions"])
    return _chat_or_http(
        _app_source(identity),
        payload,
        include_memory="memory.read" in permissions,
        include_knowledge="knowledge.search" in permissions,
        include_contacts="contacts.read" in permissions,
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
    return _conversation_payload(_app_source(identity), conversation_id)


@router.post("/api/v1/control/chat")
def control_chat(payload: ChatRequest) -> dict:
    return _chat_or_http(
        "owner",
        payload,
        include_memory=True,
        include_knowledge=True,
        include_contacts=True,
        tool_permissions=set(),
        owner_tools=True,
    )


@router.get("/api/v1/control/conversations")
def control_conversations(limit: int = Query(default=50, ge=1, le=100)) -> dict:
    return {"items": brain.list_conversations("owner", limit=limit)}


@router.get("/api/v1/control/conversations/{conversation_id}")
def control_conversation(conversation_id: str) -> dict:
    return _conversation_payload("owner", conversation_id)


@router.patch("/api/v1/control/conversations/{conversation_id}")
def control_conversation_rename(conversation_id: str, payload: ConversationRename) -> dict:
    try:
        return {"conversation": brain.rename_conversation("owner", conversation_id, payload.title)}
    except brain.BrainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/api/v1/control/conversations/{conversation_id}/context")
def control_conversation_context(conversation_id: str, payload: ContextSettingsUpdate) -> dict:
    try:
        brain.get_conversation("owner", conversation_id)
        settings = context_engine.update_settings(
            conversation_id,
            include_memory=payload.include_memory,
            include_knowledge=payload.include_knowledge,
            include_contacts=payload.include_contacts,
            cloud_allowed=payload.cloud_allowed,
            max_context_chars=payload.max_context_chars,
        )
        return {"context_settings": settings}
    except (brain.BrainError, context_engine.ContextError) as exc:
        status_code = getattr(exc, "status_code", 422)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


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


@router.get("/api/v1/control/inference")
def control_inference() -> dict:
    try:
        return providers.inference_status()
    except (providers.ProviderError, provider_secrets.ProviderSecretError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/api/v1/control/inference/preference")
def control_inference_preference(payload: InferencePreferenceUpdate) -> dict:
    try:
        providers.save_inference_settings(payload.preferred_provider)
        return providers.inference_status()
    except providers.ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/api/v1/control/inference/provider")
def control_external_provider(payload: ExternalProviderUpdate) -> dict:
    try:
        providers.save_external_provider(payload.provider_key, payload.model, payload.enabled)
        return providers.inference_status()
    except (providers.ProviderError, provider_secrets.ProviderSecretError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/control/provider-credentials")
def control_provider_credentials() -> dict:
    try:
        return provider_secrets.credential_status()
    except provider_secrets.ProviderSecretError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/api/v1/control/provider-credentials")
def control_provider_credentials_update(payload: ProviderCredentialUpdate) -> dict:
    try:
        result = provider_secrets.save_credentials(
            {
                "anthropic": payload.anthropic,
                "openai": payload.openai,
                "openrouter": payload.openrouter,
                "elevenlabs": payload.elevenlabs,
            },
            clear=payload.clear,
        )
        return {**result, "inference": providers.inference_status()}
    except (provider_secrets.ProviderSecretError, providers.ProviderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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

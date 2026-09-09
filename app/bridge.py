from __future__ import annotations

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .approvals_api import router as approvals_router
from .backups_api import router as backups_router
from .brain_api import router as brain_router
from .cognition_api import router as cognition_router
from .connected_apps_api import router as connected_apps_router
from .contacts_api import router as contacts_router
from .config import settings
from .delegation_api import router as delegation_router
from .knowledge_backup_api import router as knowledge_backup_router
from .knowledge_sources_api import router as knowledge_sources_router
from .main import app
from .remote_bridge_api import router as remote_bridge_router
from .services import providers
from .services.knowledge_backup_remote import install as install_knowledge_backup_remote_operations
from .services.pairing import DEFAULT_PERMISSIONS, pairing_status
from .system_api import router as system_router
from .tasks_api import router as tasks_router
from .tools_api import router as tools_router
from .usage_api import router as usage_router


class PairStatusRequest(BaseModel):
    request_id: str = Field(min_length=10, max_length=128)
    claim_token: str = Field(min_length=20, max_length=256)


# Extend the existing fail-closed Remote Bridge with the bounded, authenticated
# Knowledge backup operations before the worker begins serving relay requests.
install_knowledge_backup_remote_operations()

app.include_router(delegation_router)
app.include_router(brain_router)
app.include_router(tools_router)
app.include_router(approvals_router)
app.include_router(contacts_router)
app.include_router(tasks_router)
app.include_router(knowledge_sources_router)
app.include_router(knowledge_backup_router)
app.include_router(cognition_router)
app.include_router(backups_router)
app.include_router(system_router)
app.include_router(remote_bridge_router)
app.include_router(usage_router)
app.include_router(connected_apps_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.get("/api/v1/capabilities")
def capabilities() -> dict:
    inference = providers.inference_status()
    return {
        "service": settings.app_name,
        "version": settings.version,
        "pairing_protocol": "claim-v1",
        "local_bridge": True,
        "inference": {
            "available": bool(inference["available"]),
            "selected_provider": inference["selected_provider"],
            "model": inference["model"],
            "compute_source": inference["compute_source"],
            "cloud_fallback_required": bool(inference["cloud_fallback_required"]),
        },
        "permissions": sorted(DEFAULT_PERMISSIONS),
        "features": [
            "action.approvals",
            "approvals.federation.v1",
            "agent.chat",
            "agent.delegation.v1",
            "agent.context",
            "agent.context.awareness",
            "agent.context.budget",
            "agent.context.sources",
            "agent.privacy.local_only",
            "agent.tools.read",
            "app.collaboration.v1",
            "app.scopes.v1",
            "awareness.read",
            "cognition.activity_mirror",
            "cognition.event_bus",
            "cognition.jobs",
            "cognition.memory_candidates",
            "cognition.multi_app_awareness",
            "contacts.read",
            "conversations",
            "events.read",
            "events.write",
            "inference.routing",
            "inference.status",
            "knowledge.external_backup.v1",
            "knowledge.search",
            "knowledge.sources.local",
            "knowledge.sources.sync",
            "knowledge.write",
            "memory.provenance",
            "memory.read",
            "memory.write",
            "notifications.read",
            "ollama.local",
            "owner.connected_apps.v1",
            "plugins.manifest.v1",
            "plugins.read",
            "plugins.registry",
            "plugins.subscriptions",
            "plugins.tools.read_only",
            "provider.credentials",
            "remote.bridge.v1",
            "skills",
            "tasks.read",
            "tasks.write",
            "tasks.reminders",
            "tools.execute",
            "usage.history",
            "usage.sync",
            "owner.control",
        ],
    }


@app.post("/api/v1/pairing/status")
def pairing_status_endpoint(payload: PairStatusRequest) -> dict:
    result = pairing_status(payload.request_id.strip(), payload.claim_token.strip())
    if result is None:
        raise HTTPException(status_code=404, detail="Pairing request not found")
    return result

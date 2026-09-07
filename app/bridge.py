from __future__ import annotations

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .approvals_api import router as approvals_router
from .backups_api import router as backups_router
from .brain_api import router as brain_router
from .contacts_api import router as contacts_router
from .config import settings
from .main import app
from .services.pairing import DEFAULT_PERMISSIONS, pairing_status
from .system_api import router as system_router
from .tools_api import router as tools_router


class PairStatusRequest(BaseModel):
    request_id: str = Field(min_length=10, max_length=128)
    claim_token: str = Field(min_length=20, max_length=256)


app.include_router(brain_router)
app.include_router(tools_router)
app.include_router(approvals_router)
app.include_router(contacts_router)
app.include_router(backups_router)
app.include_router(system_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.get("/api/v1/capabilities")
def capabilities() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.version,
        "pairing_protocol": "claim-v1",
        "local_bridge": True,
        "permissions": sorted(DEFAULT_PERMISSIONS),
        "features": [
            "action.approvals",
            "agent.chat",
            "agent.tools.read",
            "contacts.read",
            "conversations",
            "knowledge.search",
            "memory.read",
            "memory.write",
            "ollama.local",
            "skills",
            "tools.execute",
            "owner.control",
        ],
    }


@app.post("/api/v1/pairing/status")
def pairing_status_endpoint(payload: PairStatusRequest) -> dict:
    result = pairing_status(payload.request_id.strip(), payload.claim_token.strip())
    if result is None:
        raise HTTPException(status_code=404, detail="Pairing request not found")
    return result

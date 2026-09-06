from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .database import db, initialize_database
from .services.pairing import approve_pairing, authenticate, create_pairing_request


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="HomeServer", version="0.1.0", lifespan=lifespan)


class PairRequest(BaseModel):
    app_key: str = Field(min_length=2, max_length=80)
    app_name: str = Field(min_length=2, max_length=120)
    permissions: list[str] = []


class PairApproval(BaseModel):
    code: str = Field(min_length=5, max_length=32)


def current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def require(permission: str):
    def dependency(identity: dict = Depends(current_app)) -> dict:
        if permission not in identity["permissions"]:
            raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
        return identity
    return dependency


@app.get("/api/v1/health")
def health() -> dict:
    return {"ok": True, "service": settings.app_name, "version": "0.1.0"}


@app.get("/api/v1/status")
def status() -> dict:
    with db() as connection:
        apps = connection.execute("SELECT COUNT(*) AS total FROM paired_apps WHERE status='active'").fetchone()["total"]
        memories = connection.execute("SELECT COUNT(*) AS total FROM agent_memory").fetchone()["total"]
        knowledge = connection.execute("SELECT COUNT(*) AS total FROM knowledge_items").fetchone()["total"]
    return {"service": settings.app_name, "paired_apps": apps, "memory_items": memories, "knowledge_items": knowledge}


@app.post("/api/v1/pairing/request")
def request_pairing(payload: PairRequest) -> dict:
    return create_pairing_request(payload.app_key, payload.app_name, payload.permissions)


@app.post("/api/v1/pairing/approve")
def approve(payload: PairApproval) -> dict:
    result = approve_pairing(payload.code)
    if result is None:
        raise HTTPException(status_code=404, detail="Pairing request not found or expired")
    return result


@app.get("/api/v1/me")
def me(identity: dict = Depends(current_app)) -> dict:
    return identity


@app.get("/api/v1/knowledge")
def list_knowledge(identity: dict = Depends(require("knowledge.search"))) -> dict:
    with db() as connection:
        rows = connection.execute(
            "SELECT id, title, kind, source_path, created_at, updated_at FROM knowledge_items ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
    return {"items": [dict(row) for row in rows], "app": identity["app_key"]}

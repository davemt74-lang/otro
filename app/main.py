from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .database import db, initialize_database
from .services import app_scopes
from .services.knowledge import (
    KnowledgeImportError,
    create_knowledge_item,
    delete_knowledge_item,
    ensure_knowledge_index,
    ingest_document,
    list_knowledge,
    rebuild_knowledge_index,
)
from .services.pairing import DEFAULT_PERMISSIONS, approve_pairing, authenticate, create_pairing_request

ROOT_DIR = Path(__file__).resolve().parents[1]
UI_DIR = ROOT_DIR / "ui"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    ensure_knowledge_index()
    yield


app = FastAPI(title="HomeServer", version=settings.version, lifespan=lifespan)
if UI_DIR.exists():
    app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")


class PairRequest(BaseModel):
    app_key: str = Field(min_length=2, max_length=80)
    app_name: str = Field(min_length=2, max_length=120)
    permissions: list[str] = Field(default_factory=list)


class PairApproval(BaseModel):
    code: str = Field(min_length=5, max_length=32)


class AgentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=12000)
    model: str = Field(default="", max_length=120)


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="note", min_length=1, max_length=40)
    content: str = Field(default="", max_length=250000)
    source_path: str | None = Field(default=None, max_length=1000)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50000)
    memory_key: str | None = Field(default=None, max_length=160)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    agent_id: int | None = None


class AppStatusUpdate(BaseModel):
    status: str


class PermissionUpdate(BaseModel):
    permission: str
    allowed: bool


class AppScopeUpdate(BaseModel):
    cloud_allowed: bool = True
    memory_key_prefixes: list[str] = Field(default_factory=list, max_length=32)
    knowledge_kinds: list[str] = Field(default_factory=list, max_length=32)
    tool_names: list[str] = Field(default_factory=list, max_length=32)
    plugin_keys: list[str] = Field(default_factory=list, max_length=32)


def _log(action: str, resource_type: str | None = None, resource_key: str | None = None, metadata: dict | None = None) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', ?, ?, ?, ?)
            """,
            (action, resource_type, resource_key, json.dumps(metadata or {}, separators=(",", ":"))),
        )


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


@app.get("/", include_in_schema=False)
def control_center():
    index = UI_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=503, detail="Control center assets are unavailable")
    return FileResponse(index)


@app.get("/api/v1/health")
def health() -> dict:
    return {"ok": True, "service": settings.app_name, "version": settings.version}


@app.get("/api/v1/status")
def status() -> dict:
    with db() as connection:
        apps = connection.execute("SELECT COUNT(*) AS total FROM paired_apps WHERE status='active'").fetchone()["total"]
        memories = connection.execute("SELECT COUNT(*) AS total FROM agent_memory").fetchone()["total"]
        knowledge = connection.execute("SELECT COUNT(*) AS total FROM knowledge_items").fetchone()["total"]
        unread = connection.execute("SELECT COUNT(*) AS total FROM notifications WHERE read_at IS NULL").fetchone()["total"]
        schema_version = connection.execute("SELECT COALESCE(MAX(version), 1) AS version FROM schema_migrations").fetchone()["version"]
    return {
        "service": settings.app_name,
        "version": settings.version,
        "schema_version": schema_version,
        "paired_apps": apps,
        "memory_items": memories,
        "knowledge_items": knowledge,
        "unread_notifications": unread,
    }


@app.post("/api/v1/pairing/request")
def request_pairing(payload: PairRequest) -> dict:
    return create_pairing_request(payload.app_key, payload.app_name, payload.permissions)


@app.post("/api/v1/pairing/approve")
def approve(payload: PairApproval) -> dict:
    result = approve_pairing(payload.code.strip())
    if result is None:
        raise HTTPException(status_code=404, detail="Pairing request not found or expired")
    return result


@app.get("/api/v1/me")
def me(identity: dict = Depends(current_app)) -> dict:
    return identity


@app.get("/api/v1/agent")
def client_agent(identity: dict = Depends(require("agent.chat"))) -> dict:
    with db() as connection:
        row = connection.execute("SELECT id, name, instructions, model, updated_at FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
    return {"agent": dict(row) if row else None, "app": identity["app_key"]}


@app.get("/api/v1/knowledge")
def client_knowledge(q: str = Query(default="", max_length=240), identity: dict = Depends(require("knowledge.search"))) -> dict:
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    items = [
        item for item in list_knowledge(q, limit=100)
        if app_scopes.knowledge_kind_allowed(scope, item.get("kind"))
    ]
    return {"items": items, "app": identity["app_key"]}


@app.get("/api/v1/memory")
def client_memory(identity: dict = Depends(require("memory.read"))) -> dict:
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    with db() as connection:
        rows = connection.execute("SELECT id, agent_id, memory_key, content, importance, created_at, updated_at FROM agent_memory ORDER BY importance DESC, updated_at DESC LIMIT 200").fetchall()
    items = [dict(row) for row in rows if app_scopes.memory_key_allowed(scope, row["memory_key"])]
    return {"items": items, "app": identity["app_key"]}


@app.post("/api/v1/memory")
def client_memory_write(payload: MemoryCreate, identity: dict = Depends(require("memory.write"))) -> dict:
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    if not app_scopes.memory_key_allowed(scope, payload.memory_key):
        raise HTTPException(status_code=403, detail="Memory key is outside this application's allowed scope")
    with db() as connection:
        cursor = connection.execute("INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, ?, ?, ?)", (payload.agent_id, payload.memory_key, payload.content.strip(), payload.importance))
        memory_id = cursor.lastrowid
        connection.execute("INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key) VALUES ('app', ?, 'memory.created', 'memory', ?)", (identity["app_key"], str(memory_id)))
    return {"id": memory_id, "created": True}


@app.get("/api/v1/control/overview")
def control_overview() -> dict:
    with db() as connection:
        agent = connection.execute("SELECT id, name, instructions, model, updated_at FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
        counts = {
            "paired_apps": connection.execute("SELECT COUNT(*) FROM paired_apps WHERE status='active'").fetchone()[0],
            "memory_items": connection.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0],
            "knowledge_items": connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0],
            "pending_pairing": connection.execute("SELECT COUNT(*) FROM pairing_requests WHERE status='pending'").fetchone()[0],
        }
        activity = connection.execute("SELECT id, actor_type, actor_key, action, resource_type, resource_key, created_at FROM activity_log ORDER BY id DESC LIMIT 8").fetchall()
    return {"agent": dict(agent) if agent else None, "counts": counts, "activity": [dict(row) for row in activity]}


@app.get("/api/v1/control/agent")
def control_agent() -> dict:
    with db() as connection:
        row = connection.execute("SELECT id, name, instructions, model, created_at, updated_at FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
    return {"agent": dict(row) if row else None}


@app.put("/api/v1/control/agent")
def control_agent_update(payload: AgentUpdate) -> dict:
    with db() as connection:
        row = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
        if row is None:
            cursor = connection.execute("INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, ?, ?, 1)", (payload.name.strip(), payload.instructions.strip(), payload.model.strip()))
            agent_id = cursor.lastrowid
        else:
            agent_id = row["id"]
            connection.execute("UPDATE agents SET name=?, instructions=?, model=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (payload.name.strip(), payload.instructions.strip(), payload.model.strip(), agent_id))
    _log("agent.updated", "agent", str(agent_id))
    return {"updated": True, "id": agent_id}


@app.get("/api/v1/control/knowledge")
def control_knowledge(q: str = Query(default="", max_length=240)) -> dict:
    return {"items": list_knowledge(q, limit=250), "query": q.strip()}


@app.post("/api/v1/control/knowledge")
def control_knowledge_create(payload: KnowledgeCreate) -> dict:
    result = create_knowledge_item(payload.title, payload.kind, payload.content, payload.source_path)
    _log("knowledge.created", "knowledge", str(result["id"]), {"kind": payload.kind, "chunks": result["chunk_count"]})
    return result


@app.post("/api/v1/control/knowledge/import")
async def control_knowledge_import(file: UploadFile = File(...)) -> dict:
    data = await file.read(settings.max_upload_bytes + 1)
    try:
        result = ingest_document(file.filename or "", file.content_type, data)
    except KnowledgeImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    action = "knowledge.duplicate" if result.get("duplicate") else "knowledge.imported"
    _log(action, "knowledge", str(result["id"]), {"file": result.get("original_name"), "size_bytes": result.get("size_bytes"), "chunks": result.get("chunk_count")})
    return result


@app.post("/api/v1/control/knowledge/reindex")
def control_knowledge_reindex() -> dict:
    result = rebuild_knowledge_index()
    _log("knowledge.reindexed", "knowledge", None, result)
    return {"reindexed": True, **result}


@app.delete("/api/v1/control/knowledge/{item_id}")
def control_knowledge_delete(item_id: int) -> dict:
    if not delete_knowledge_item(item_id):
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    _log("knowledge.deleted", "knowledge", str(item_id))
    return {"deleted": True}


@app.get("/api/v1/control/memory")
def control_memory() -> dict:
    with db() as connection:
        rows = connection.execute("SELECT m.id, m.agent_id, a.name AS agent_name, m.memory_key, m.content, m.importance, m.created_at, m.updated_at FROM agent_memory m LEFT JOIN agents a ON a.id=m.agent_id ORDER BY m.importance DESC, m.id DESC LIMIT 250").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/v1/control/memory")
def control_memory_create(payload: MemoryCreate) -> dict:
    agent_id = payload.agent_id
    with db() as connection:
        if agent_id is None:
            primary = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
            agent_id = primary["id"] if primary else None
        cursor = connection.execute("INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, ?, ?, ?)", (agent_id, payload.memory_key, payload.content.strip(), payload.importance))
        memory_id = cursor.lastrowid
    _log("memory.created", "memory", str(memory_id))
    return {"created": True, "id": memory_id}


@app.delete("/api/v1/control/memory/{memory_id}")
def control_memory_delete(memory_id: int) -> dict:
    with db() as connection:
        cursor = connection.execute("DELETE FROM agent_memory WHERE id=?", (memory_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Memory item not found")
    _log("memory.deleted", "memory", str(memory_id))
    return {"deleted": True}


@app.get("/api/v1/control/apps")
def control_apps() -> dict:
    with db() as connection:
        apps = connection.execute("SELECT id, app_key, name, status, paired_at, last_seen_at FROM paired_apps ORDER BY id DESC").fetchall()
        result = []
        for app_row in apps:
            permissions = connection.execute("SELECT permission, allowed FROM app_permissions WHERE paired_app_id=? ORDER BY permission", (app_row["id"],)).fetchall()
            item = dict(app_row)
            item["permissions"] = [dict(row) for row in permissions]
            item["scope"] = app_scopes.get_scope(int(app_row["id"]))
            result.append(item)
        pending = connection.execute("SELECT id, app_key, app_name, requested_permissions, status, expires_at, created_at FROM pairing_requests WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    pending_items = []
    for row in pending:
        item = dict(row)
        item["requested_permissions"] = json.loads(item["requested_permissions"] or "[]")
        pending_items.append(item)
    return {"apps": result, "pending": pending_items, "available_permissions": sorted(DEFAULT_PERMISSIONS)}


@app.patch("/api/v1/control/apps/{app_id}")
def control_app_status(app_id: int, payload: AppStatusUpdate) -> dict:
    if payload.status not in {"active", "paused", "revoked"}:
        raise HTTPException(status_code=422, detail="Invalid app status")
    with db() as connection:
        cursor = connection.execute("UPDATE paired_apps SET status=? WHERE id=?", (payload.status, app_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Connected app not found")
    _log("app.status", "app", str(app_id), {"status": payload.status})
    return {"updated": True, "status": payload.status}


@app.put("/api/v1/control/apps/{app_id}/permission")
def control_app_permission(app_id: int, payload: PermissionUpdate) -> dict:
    if payload.permission not in DEFAULT_PERMISSIONS:
        raise HTTPException(status_code=422, detail="Unknown permission")
    with db() as connection:
        exists = connection.execute("SELECT id FROM paired_apps WHERE id=?", (app_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Connected app not found")
        connection.execute("""
            INSERT INTO app_permissions(paired_app_id, permission, allowed)
            VALUES (?, ?, ?)
            ON CONFLICT(paired_app_id, permission)
            DO UPDATE SET allowed=excluded.allowed, updated_at=CURRENT_TIMESTAMP
        """, (app_id, payload.permission, 1 if payload.allowed else 0))
    _log("app.permission", "app", str(app_id), {"permission": payload.permission, "allowed": payload.allowed})
    return {"updated": True}


@app.put("/api/v1/control/apps/{app_id}/scope")
def control_app_scope(app_id: int, payload: AppScopeUpdate) -> dict:
    try:
        scope = app_scopes.save_scope(app_id, payload.model_dump())
    except app_scopes.ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _log(
        "app.scope",
        "app",
        str(app_id),
        {
            "cloud_allowed": scope["cloud_allowed"],
            "memory_prefix_count": len(scope["memory_key_prefixes"]),
            "knowledge_kind_count": len(scope["knowledge_kinds"]),
            "tool_count": len(scope["tool_names"]),
            "plugin_count": len(scope["plugin_keys"]),
        },
    )
    return {"updated": True, "scope": scope}


@app.get("/api/v1/control/activity")
def control_activity(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    with db() as connection:
        rows = connection.execute("SELECT id, actor_type, actor_key, action, resource_type, resource_key, metadata_json, created_at FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
            item.pop("metadata_json", None)
        items.append(item)
    return {"items": items}


@app.get("/api/v1/control/notifications")
def control_notifications() -> dict:
    with db() as connection:
        rows = connection.execute("SELECT id, source, title, body, level, read_at, created_at FROM notifications ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(row) for row in rows]}

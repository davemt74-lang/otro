from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .services import onboarding_chat
from .database import db, initialize_database
from .services import activity_center, ambient_agent, ambient_orchestration, app_scopes, automation_intelligence, device_rollout, federated_data, hardware_adapters, hardware_experience, homeserver_app_runtime, hosting_health_recovery, local_automation, memory_continuity, physical_agent, physical_meeting, tracky_cross_site_presence, tracky_federated_automation, tracky_federation_access_operations, tracky_federation_agent_health, tracky_federation_fleet_health, tracky_federation_governed_operations, tracky_federation_operations, tracky_physical_world_dashboard, tracky_sync_visibility
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
    from .services import agent_mission_runtime
    agent_mission_runtime.recover_interrupted()
    from .services import agent_mission_browser
    agent_mission_browser.recover_interrupted()
    from .services import agent_mission_live_browser
    agent_mission_live_browser.recover_interrupted()
    from .services import agent_mission_browser_plans
    agent_mission_browser_plans.recover_interrupted()
    onboarding_chat.resume_approved()
    ensure_knowledge_index()
    tracky_federated_automation.recover_incomplete_runs()
    device_rollout.reconcile_update_results()
    device_rollout.start()
    hardware_adapters.start()
    physical_agent.start()
    hardware_experience.start()
    physical_meeting.start_runtime()
    ambient_agent.start()
    tracky_federation_agent_health.start()
    local_automation.start()
    automation_intelligence.start()
    ambient_orchestration.start()
    hosting_health_recovery.start()
    homeserver_app_runtime.start()
    from .services import workspace_sync
    workspace_sync.start()
    from .services import agent_mission_schedules
    agent_mission_schedules.start()
    try:
        yield
    finally:
        agent_mission_schedules.stop()
        workspace_sync.stop()
        agent_mission_browser_plans.shutdown()
        agent_mission_runtime.shutdown()
        homeserver_app_runtime.stop()
        hosting_health_recovery.stop()
        ambient_orchestration.stop()
        automation_intelligence.stop()
        local_automation.stop()
        tracky_federation_agent_health.stop()
        ambient_agent.stop()
        physical_meeting.stop_runtime()
        hardware_experience.stop()
        physical_agent.stop()
        hardware_adapters.stop()
        device_rollout.stop()


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


class FederatedAutomationDefinitionRequest(BaseModel):
    automation_id: str = Field(default="", max_length=128)
    revision: int = Field(default=0, ge=0)
    idempotency_key: str = Field(default="", max_length=160)
    name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=1000)
    origin_site_id: str = Field(min_length=36, max_length=64)
    state: str = Field(default="draft", max_length=30)
    trigger: dict = Field(default_factory=dict)
    steps: list[dict] = Field(default_factory=list, min_length=1, max_length=64)
    participating_site_ids: list[str] = Field(default_factory=list, max_length=64)
    participating_device_ids: list[str] = Field(default_factory=list, max_length=128)
    approval_policy: str = Field(default="governed", max_length=30)
    default_deadline_ms: int = Field(default=0, ge=0)


class FederatedAutomationRunRequest(BaseModel):
    automation_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(default="", max_length=160)
    idempotency_key: str = Field(default="", max_length=160)
    trigger_event_id: str = Field(default="", max_length=160)
    deadline_at_ms: int = Field(default=0, ge=0)


class FederatedAutomationCancelRequest(BaseModel):
    reason: str = Field(default="", max_length=240)


class FederationGovernedOperationRequest(BaseModel):
    operation_type: str = Field(min_length=3, max_length=40)
    target_site_id: str = Field(min_length=36, max_length=64)
    device_id: str = Field(default="", max_length=80)
    new_authority_device_id: str = Field(default="", max_length=80)
    confirmation_token: str = Field(default="", max_length=160)
    idempotency_key: str = Field(default="", max_length=160)
    require_approval: bool = False
    expires_at_ms: int = Field(default=0, ge=0)
    parameters: dict = Field(default_factory=dict)


class FederationOperationDecision(BaseModel):
    approved: bool


class FederationSitePolicyUpdate(BaseModel):
    mode: str = Field(default="private", max_length=30)
    allow_federation: bool = False
    allow_remote_observation: bool = False
    default_identity_visibility: str = Field(default="none", max_length=30)
    allowed_peer_sites: list[str] = Field(default_factory=list, max_length=128)


class FederationPermissionOperation(BaseModel):
    action: str = Field(min_length=4, max_length=20)
    destination_site_id: str = Field(min_length=36, max_length=64)
    scope: str = Field(min_length=3, max_length=80)
    reason: str = Field(default="", max_length=200)


class FederationConsentOperation(BaseModel):
    canonical_identity_id: str = Field(min_length=36, max_length=64)
    scope: str = Field(min_length=3, max_length=80)
    status: str = Field(min_length=3, max_length=30)
    reason: str = Field(default="", max_length=200)


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
    from .services.runtime_build import runtime_build_id
    return {"ok": True, "service": settings.app_name, "version": settings.version, "build_id": runtime_build_id()}


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
    # Import lazily to avoid coupling the core application module to the
    # optional v0.37 router during early startup. All authenticated knowledge
    # reads use the same collection + knowledge-kind scope contract.
    from .knowledge_collections_api import scoped_knowledge_search

    try:
        result = scoped_knowledge_search(identity, q, 50)
    except Exception as exc:
        if hasattr(exc, "status_code"):
            raise HTTPException(status_code=int(getattr(exc, "status_code")), detail=str(exc)) from exc
        raise
    return {**result, "app": identity["app_key"]}


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


def _federated_automation_guard(callable_):
    try:
        return callable_()
    except tracky_federated_automation.FederatedAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _federation_operation_guard(callable_):
    try:
        return callable_()
    except tracky_federation_governed_operations.FederationOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/v1/control/federation-operations")
def control_federation_operations() -> dict:
    return {
        "operations": tracky_federation_operations.current_report(),
        "capability": tracky_federation_operations.public_capability(),
    }


@app.get("/api/v1/control/federation-sync-visibility")
def control_federation_sync_visibility() -> dict:
    return {
        "visibility": tracky_sync_visibility.current_report(),
        "capability": tracky_sync_visibility.public_capability(),
    }


@app.get("/api/v1/control/federation-access")
def control_federation_access() -> dict:
    return {
        "access": tracky_federation_access_operations.current_report(),
        "capability": tracky_federation_access_operations.public_capability(),
    }


@app.get("/api/v1/control/federation-agent-health")
def control_federation_agent_health() -> dict:
    return {
        "health": tracky_federation_agent_health.current_report(),
        "history": tracky_federation_agent_health.recent_history(100),
        "capability": tracky_federation_agent_health.public_capability(),
    }


@app.get("/api/v1/control/federation-fleet-health")
def control_federation_fleet_health() -> dict:
    return {
        "fleet_health": tracky_federation_fleet_health.current_report(),
        "capability": tracky_federation_fleet_health.public_capability(),
    }


@app.get("/api/v1/control/federated-automation")
def control_federated_automation() -> dict:
    return {"automation": _federated_automation_guard(tracky_federated_automation.report), "capability": tracky_federated_automation.public_capability()}


@app.post("/api/v1/control/federated-automation/definitions")
def control_federated_automation_definition(payload: FederatedAutomationDefinitionRequest) -> dict:
    result=_federated_automation_guard(lambda: tracky_federated_automation.create_definition(payload.model_dump(),actor={"actor_type":"owner","actor_id":"local_owner"}))
    _log("tracky.federated_automation.definition.created","tracky_federated_automation",result["automation_id"],{"revision":result["revision"]})
    return {"definition":result}


@app.post("/api/v1/control/federated-automation/runs")
def control_federated_automation_run(payload: FederatedAutomationRunRequest) -> dict:
    result=_federated_automation_guard(lambda: tracky_federated_automation.create_run(payload.model_dump(),actor={"actor_type":"owner","actor_id":"local_owner"}))
    _log("tracky.federated_automation.run.created","tracky_federated_automation_run",result["run_id"],{"automation_id":result["automation_id"]})
    return {"run":result}


@app.post("/api/v1/control/federated-automation/runs/{run_id}/cancel")
def control_federated_automation_cancel(run_id: str,payload: FederatedAutomationCancelRequest) -> dict:
    result=_federated_automation_guard(lambda: tracky_federated_automation.cancel_run(run_id,reason=payload.reason,actor={"actor_type":"owner","actor_id":"local_owner"}))
    _log("tracky.federated_automation.run.cancelled","tracky_federated_automation_run",run_id,{"reason":payload.reason})
    return {"run":result}


@app.post("/api/v1/control/federated-automation/recover")
def control_federated_automation_recover() -> dict:
    return _federated_automation_guard(tracky_federated_automation.recover_incomplete_runs)


@app.get("/api/v1/control/federation-governed-operations")
def control_federation_governed_operations() -> dict:
    return {"operations": _federation_operation_guard(tracky_federation_governed_operations.report), "capability": tracky_federation_governed_operations.public_capability()}


@app.post("/api/v1/control/federation-governed-operations/propose")
def control_federation_operation_propose(payload: FederationGovernedOperationRequest) -> dict:
    result=_federation_operation_guard(lambda: tracky_federation_governed_operations.propose(payload.model_dump(),actor={"actor_type":"owner","actor_id":"local_owner"}))
    _log("tracky.federation_operation.proposed","tracky_federation_operation",result["request_id"],{"operation_type":result["operation_type"]})
    return {"operation":result}


@app.post("/api/v1/control/federation-governed-operations/{request_id}/decision")
def control_federation_operation_decision(request_id: str,payload: FederationOperationDecision) -> dict:
    result=_federation_operation_guard(lambda: tracky_federation_governed_operations.decide(request_id,payload.approved,actor={"actor_type":"owner","actor_id":"local_owner"}))
    _log("tracky.federation_operation.decision","tracky_federation_operation",request_id,{"approved":payload.approved})
    return {"operation":result}


@app.post("/api/v1/control/federation-governed-operations/{request_id}/execute")
def control_federation_operation_execute(request_id: str) -> dict:
    result=_federation_operation_guard(lambda: tracky_federation_governed_operations.execute(request_id))
    _log("tracky.federation_operation.executed","tracky_federation_operation",request_id,{"state":result["state"]})
    return {"operation":result}


@app.post("/api/v1/control/federation-governed-operations/{request_id}/refresh")
def control_federation_operation_refresh(request_id: str) -> dict:
    return {"operation":_federation_operation_guard(lambda: tracky_federation_governed_operations.refresh_reconciliation(request_id))}


@app.post("/api/v1/control/federation-governed-operations/{request_id}/cancel")
def control_federation_operation_cancel(request_id: str) -> dict:
    result=_federation_operation_guard(lambda: tracky_federation_governed_operations.cancel(request_id))
    _log("tracky.federation_operation.cancelled","tracky_federation_operation",request_id)
    return {"operation":result}


@app.put("/api/v1/control/federation-access/site-policy")
def control_federation_access_site_policy(payload: FederationSitePolicyUpdate) -> dict:
    result = tracky_federation_access_operations.update_site_policy(payload.model_dump())
    _log("tracky.federation_site_policy.updated", "tracky_federation_policy", result["access"].get("local_site_id") or "")
    return result


@app.post("/api/v1/control/federation-access/permission")
def control_federation_access_permission(payload: FederationPermissionOperation) -> dict:
    result = tracky_federation_access_operations.set_permission(payload.model_dump())
    _log("tracky.federation_permission." + payload.action.strip().lower(), "tracky_federation_permission", payload.destination_site_id)
    return result


@app.post("/api/v1/control/federation-access/consent")
def control_federation_access_consent(payload: FederationConsentOperation) -> dict:
    result = tracky_federation_access_operations.set_consent(payload.model_dump())
    _log("tracky.federation_consent." + payload.status.strip().lower(), "tracky_recognition_consent", payload.canonical_identity_id)
    return result


@app.get("/api/v1/control/physical-world-dashboard")
def control_physical_world_dashboard(site: str = Query(default="", max_length=64)) -> dict:
    return {
        "dashboard": tracky_physical_world_dashboard.current_report(site),
        "capability": tracky_physical_world_dashboard.public_capability(),
    }


@app.get("/api/v1/control/cross-site-presence")
def control_cross_site_presence() -> dict:
    return {
        "presence": tracky_cross_site_presence.current_report(),
        "capability": tracky_cross_site_presence.public_capability(),
    }


@app.get("/api/v1/control/knowledge")
def control_knowledge(q: str = Query(default="", max_length=240), cloud_offset: int = Query(default=0,ge=0)) -> dict:
    from .services import native_workspaces
    cloud = native_workspaces.items('knowledge',q,offset=cloud_offset,limit=250)
    local = list_knowledge(q, limit=250)
    return {"items":local+[{**row,'kind':row.get('file_type') or ('Cloud folder' if row['table']=='artist_transcript_folders_v177' else 'Cloud document')} for row in cloud['items']],
            "query":q.strip(),"sources":{"homeserver":len(local),"vp3_cloud":cloud['count']},"cloud_count":cloud['count'],"synced_at":cloud['synced_at']}


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
    items = memory_continuity.list_federated_memories(
        "",
        100,
        source_app_key="owner",
        owner=True,
    )
    with db() as connection:
        agent_rows = connection.execute("SELECT id,name FROM agents").fetchall()
    names = {int(row["id"]): str(row["name"]) for row in agent_rows}
    for item in items:
        agent_id = item.get("agent_id")
        item["agent_name"] = names.get(int(agent_id)) if agent_id is not None else None
    return {"items": items}


@app.post("/api/v1/control/memory")
def control_memory_create(payload: MemoryCreate) -> dict:
    agent_id = payload.agent_id
    with db() as connection:
        if agent_id is None:
            primary = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
            agent_id = primary["id"] if primary else None
        cursor = connection.execute("INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, ?, ?, ?)", (agent_id, payload.memory_key, payload.content.strip(), payload.importance))
        memory_id = cursor.lastrowid
    projected = memory_continuity.get_federated_memory(int(memory_id))
    _log("memory.created", "memory", str(memory_id), {
        "canonical_id": str((projected or {}).get("canonical_id") or "")[:45],
    })
    return {"created": True, "id": memory_id, "memory": projected}


@app.delete("/api/v1/control/memory/{memory_id}")
def control_memory_delete(memory_id: int) -> dict:
    current = memory_continuity.get_federated_memory(memory_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    with db() as connection:
        cursor = connection.execute("DELETE FROM agent_memory WHERE id=?", (memory_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Memory item not found")
    federated_data.mark_tombstone(
        "homeserver",
        "memory",
        str(current["authority_key"]),
        observed_source="homeserver",
    )
    _log("memory.deleted", "memory", str(memory_id), {
        "canonical_id": str(current["canonical_id"])[:45],
    })
    return {"deleted": True, "canonical_id": current["canonical_id"]}


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
def control_activity(
    limit: int = Query(default=100, ge=1, le=500),
    category: str = Query(default="", max_length=40),
    needs_attention: bool = False,
    unread_only: bool = False,
) -> dict:
    return activity_center.list_activity(
        limit=limit,
        category=category,
        needs_attention=needs_attention,
        unread_only=unread_only,
    )


@app.get("/api/v1/control/notifications")
def control_notifications(
    unread_only: bool = False,
    needs_attention: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    activity_center.sync_notifications()
    feed=activity_center.list_activity(
        limit=limit,
        category="notifications",
        needs_attention=needs_attention,
        unread_only=unread_only,
    )
    return {"items":feed["items"],"count":feed["count"]}

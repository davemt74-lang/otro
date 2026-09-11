from __future__ import annotations

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .action_policy_api import router as action_policy_router
from .agent_routing_api import router as agent_routing_router
from .agents_api import router as agents_router
from .approvals_api import router as approvals_router
from .backups_api import router as backups_router
from .brain_api import router as brain_router
from .capability_registry_api import router as capability_registry_router
from .cognition_api import router as cognition_router
from .connected_apps_api import router as connected_apps_router
from .contacts_api import router as contacts_router
from .config import settings
from .delegation_api import router as delegation_router
from .files_api import router as files_router
from .handoffs_api import router as handoffs_router
from .knowledge_backup_api import router as knowledge_backup_router
from .knowledge_collections_api import router as knowledge_collections_router
from .knowledge_sources_api import router as knowledge_sources_router
from .local_apps_api import router as local_apps_router
from .local_voice_api import router as local_voice_router
from .main import app
from .remote_bridge_api import router as remote_bridge_router
from .services import providers
from .services.knowledge_backup_remote import install as install_knowledge_backup_remote_operations
from .services.knowledge_collections_remote import install as install_knowledge_collection_remote_operations
from .services.local_file_actions_agent import install as install_local_file_action_agent_tools
from .services.local_file_actions_approvals import install as install_local_file_action_approvals
from .services.local_file_actions_registry import install as install_local_file_action_registry
from .services.local_file_actions_remote import install as install_local_file_action_remote_operations
from .services.local_file_actions_tools import install as install_local_file_action_tools
from .services.local_files_agent import install as install_local_file_agent_tools
from .services.local_files_remote import install as install_local_files_remote_operations
from .services.pairing import DEFAULT_PERMISSIONS, pairing_status
from .system_api import router as system_router
from .tasks_api import router as tasks_router
from .team_planning_api import router as team_planning_router
from .team_runs_api import router as team_runs_router
from .tools_api import router as tools_router
from .usage_api import router as usage_router
from .workflow_timeline_api import router as workflow_timeline_router


class PairStatusRequest(BaseModel):
    request_id: str = Field(min_length=10, max_length=128)
    claim_token: str = Field(min_length=20, max_length=256)


# Extend the existing fail-closed capability surfaces before the worker begins
# serving requests. Write actions remain policy/approval gated and never accept
# caller filesystem paths.
install_knowledge_backup_remote_operations()
install_knowledge_collection_remote_operations()
install_local_files_remote_operations()
install_local_file_action_remote_operations()
install_local_file_action_tools()
install_local_file_action_approvals()
install_local_file_agent_tools()
install_local_file_action_agent_tools()
install_local_file_action_registry()

app.include_router(agents_router)
app.include_router(agent_routing_router)
app.include_router(delegation_router)
app.include_router(handoffs_router)
app.include_router(workflow_timeline_router)
app.include_router(team_runs_router)
app.include_router(team_planning_router)
app.include_router(brain_router)
app.include_router(tools_router)
app.include_router(action_policy_router)
app.include_router(approvals_router)
app.include_router(contacts_router)
app.include_router(tasks_router)
app.include_router(knowledge_sources_router)
app.include_router(knowledge_collections_router)
app.include_router(files_router)
app.include_router(knowledge_backup_router)
app.include_router(cognition_router)
app.include_router(backups_router)
app.include_router(system_router)
app.include_router(remote_bridge_router)
app.include_router(usage_router)
app.include_router(connected_apps_router)
app.include_router(local_apps_router)
app.include_router(local_voice_router)
app.include_router(capability_registry_router)

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
        "capability_registry": {"version": "v0.33", "operation": "capability.registry", "authenticated": True},
        "local_apps": {"version": "v0.40", "owner_managed": True, "catalog": "embedded-sha256-pinned"},
        "local_voice": {
            "version": "v0.41",
            "owner_api": True,
            "local_only": True,
            "operations": ["speech.transcribe", "speech.synthesize"],
        },
        "agent_personas": {
            "version": "v0.46",
            "owner_managed": True,
            "multi_agent": True,
            "voice_profiles": "v0.45",
        },
        "agent_routing": {
            "version": "v0.47",
            "owner_selectable": True,
            "paired_app_scoped": True,
            "primary_agent_implicit": True,
            "conversation_bound": True,
            "voice_profiles": "v0.45",
        },
        "agent_workflows": {
            "version": "v0.48",
            "owner_managed": True,
            "paired_app_scoped": True,
            "persistent_tasks": True,
            "model_delegation_tool": True,
            "nested_delegation": False,
            "requires_agent_routing": "v0.47",
        },
        "agent_handoffs": {
            "version": "v0.49",
            "explicit": True,
            "one_shot": True,
            "conversation_bound": True,
            "paired_app_scoped": True,
            "context_budgeted": True,
            "requires_agent_workflows": "v0.48",
        },
        "agent_workflow_timeline": {
            "version": "v0.50",
            "conversation_timeline": True,
            "batch_synthesis": True,
            "max_synthesis_results": 4,
            "atomic_prepare": True,
            "paired_app_scoped": True,
            "requires_agent_handoffs": "v0.49",
        },
        "agent_team_runs": {
            "version": "v0.51",
            "min_members": 2,
            "max_members": 4,
            "distinct_specialists": True,
            "partial_failure_recovery": True,
            "retry_failed_member": True,
            "explicit_synthesis_prepare": True,
            "nested_delegation": False,
            "paired_app_scoped": True,
            "requires_workflow_timeline": "v0.50",
        },
        "agent_team_planning": {
            "version": "v0.52",
            "min_members": 2,
            "max_members": 4,
            "proposal_only": True,
            "editable_before_approval": True,
            "explicit_approval": True,
            "atomic_approval": True,
            "auto_execute": False,
            "privacy_routed": True,
            "paired_app_scoped": True,
            "nested_delegation": False,
            "requires_team_runs": "v0.51",
        },
        "action_policy": {
            "version": "v0.35",
            "operation": "tools.list",
            "embedded": True,
            "owner_managed": True,
        },
        "knowledge": {
            "version": "v0.37",
            "local_index": True,
            "collections": True,
            "collection_scopes": True,
            "citation_safe_search": True,
            "remote_operation": "knowledge.search",
        },
        "files": {
            "version": "v0.39",
            "read_version": "v0.38",
            "action_version": "v0.39",
            "permissions": ["files.read", "files.write"],
            "read_only_api": True,
            "write_policy_gated": True,
            "indexed_text_only": True,
            "collection_scoped": True,
            "arbitrary_paths": False,
            "operations": ["files.list", "files.read", "files.update", "files.delete"],
        },
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
            "action.policy.v1",
            "approvals.federation.v1",
            "agent.chat",
            "agent.delegation.v1",
            "agent.workflows.v048",
            "agent.workflows.model_delegate",
            "agent.handoffs.v049",
            "agent.workflows.timeline.v050",
            "agent.workflows.synthesis.v050",
            "agent.workflows.team_runs.v051",
            "agent.workflows.team_retry.v051",
            "agent.workflows.team_planning.v052",
            "agent.workflows.team_approval.v052",
            "agent.context",
            "agent.context.awareness",
            "agent.context.budget",
            "agent.context.sources",
            "agent.personas.v046",
            "agent.privacy.local_only",
            "agent.routing.v047",
            "agent.tools.read",
            "app.collaboration.v1",
            "app.scopes.v1",
            "awareness.read",
            "capability.registry.v1",
            "cognition.activity_mirror",
            "cognition.event_bus",
            "cognition.jobs",
            "cognition.memory_candidates",
            "cognition.multi_app_awareness",
            "contacts.read",
            "conversations",
            "events.read",
            "events.write",
            "files.actions.v1",
            "files.approval_gated.v1",
            "files.governed.v1",
            "files.indexed_text.v1",
            "files.read",
            "files.write",
            "inference.routing",
            "inference.status",
            "knowledge.citations.v1",
            "knowledge.collections.v1",
            "knowledge.external_backup.v1",
            "knowledge.search",
            "knowledge.search.scoped.v037",
            "knowledge.sources.local",
            "knowledge.sources.sync",
            "knowledge.write",
            "local.apps.v1",
            "local.apps.install.v1",
            "local.voice.runtime.v1",
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

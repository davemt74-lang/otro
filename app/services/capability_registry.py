from __future__ import annotations

from typing import Any

from ..config import settings
from ..database import db
from . import app_scopes, plugins, providers, tools
from .knowledge_sources import list_sources
from .remote_bridge import bridge_status

REGISTRY_VERSION = "v0.33"


def _safe_provider(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": str(item.get("provider_key") or "")[:80],
        "name": str(item.get("name") or item.get("provider_key") or "")[:120],
        "kind": str(item.get("kind") or "")[:80],
        "model": str(item.get("model") or "")[:200],
        "enabled": bool(item.get("enabled")),
        "ready": bool(item.get("ready")),
        "compute_source": str(item.get("compute_source") or "")[:80],
    }


def _inference_inventory() -> dict[str, Any]:
    status = providers.inference_status()
    provider_items = [_safe_provider(item) for item in status.get("providers", []) if isinstance(item, dict)]
    installed_local_models: list[str] = []
    ollama = next((item for item in provider_items if item["key"] == "ollama"), None)
    if ollama and ollama["enabled"]:
        try:
            discovered = providers.discover_ollama_models()
            for model in discovered.get("models", []) if isinstance(discovered, dict) else []:
                name = str(model or "").strip()[:200]
                if name and name not in installed_local_models:
                    installed_local_models.append(name)
                if len(installed_local_models) >= 100:
                    break
        except Exception:
            pass
    return {
        "available": bool(status.get("available")),
        "preferred_provider": str(status.get("preferred_provider") or "auto")[:80],
        "selected_provider": str(status.get("selected_provider") or "")[:80],
        "model": str(status.get("model") or "")[:200],
        "compute_source": str(status.get("compute_source") or "")[:80],
        "cloud_fallback_required": bool(status.get("cloud_fallback_required")),
        "providers": provider_items,
        "installed_local_models": installed_local_models,
    }


def _primary_brain() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT id, name, model, updated_at FROM agents WHERE is_primary=1 LIMIT 1"
        ).fetchone()
    return {
        "available": row is not None,
        "primary": {
            "id": int(row["id"]),
            "name": str(row["name"] or "")[:120],
            "model": str(row["model"] or "")[:200],
            "updated_at": row["updated_at"],
        } if row else None,
    }


def _memory_inventory(scope: dict[str, Any], permissions: set[str]) -> dict[str, Any]:
    readable = "memory.read" in permissions
    writable = "memory.write" in permissions
    count = 0
    if readable:
        with db() as connection:
            rows = connection.execute("SELECT memory_key FROM agent_memory").fetchall()
        count = sum(1 for row in rows if app_scopes.memory_key_allowed(scope, row["memory_key"]))
    return {
        "readable": readable,
        "writable": writable,
        "visible_items": count,
        "restricted": bool(app_scopes.normalize(scope)["memory_key_prefixes"]),
    }


def _knowledge_inventory(scope: dict[str, Any], permissions: set[str]) -> dict[str, Any]:
    searchable = "knowledge.search" in permissions
    writable = "knowledge.write" in permissions
    count = 0
    kinds: list[str] = []
    if searchable:
        with db() as connection:
            rows = connection.execute("SELECT kind FROM knowledge_items").fetchall()
        for row in rows:
            kind = str(row["kind"] or "")
            if not app_scopes.knowledge_kind_allowed(scope, kind):
                continue
            count += 1
            if kind and kind not in kinds:
                kinds.append(kind)
    return {
        "searchable": searchable,
        "writable": writable,
        "visible_items": count,
        "visible_kinds": sorted(kinds)[:100],
        "restricted": bool(app_scopes.normalize(scope)["knowledge_kinds"]),
    }


def _file_inventory(permissions: set[str]) -> dict[str, Any]:
    if "knowledge.search" not in permissions:
        return {"available": False, "source_count": 0, "enabled_sources": 0, "tracked_files": 0, "indexed_files": 0}
    try:
        sources = list_sources()
    except Exception:
        sources = []
    return {
        "available": True,
        "source_count": len(sources),
        "enabled_sources": sum(1 for item in sources if item.get("enabled")),
        "tracked_files": sum(max(0, int(item.get("tracked_files") or 0)) for item in sources),
        "indexed_files": sum(max(0, int(item.get("indexed_files") or 0)) for item in sources),
        "supported_extensions": [".txt", ".md", ".pdf", ".docx", ".json", ".csv"],
    }


def _contact_inventory(permissions: set[str]) -> dict[str, Any]:
    readable = "contacts.read" in permissions
    count = 0
    if readable:
        with db() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0])
    return {"readable": readable, "visible_contacts": count}


def _tool_inventory(scope: dict[str, Any], permissions: set[str]) -> list[dict[str, Any]]:
    items = [item for item in tools.list_tools(permissions) if app_scopes.tool_allowed(scope, item.get("key"))]
    return [{
        "key": str(item.get("key") or "")[:80],
        "name": str(item.get("name") or "")[:160],
        "mode": str(item.get("mode") or "")[:20],
        "enabled": bool(item.get("enabled")),
        "available": bool(item.get("available")),
        "required_permissions": [str(value)[:120] for value in item.get("required_permissions", []) if isinstance(value, str)][:30],
    } for item in items[:100]]


def _skill_inventory(scope: dict[str, Any], permissions: set[str]) -> list[dict[str, Any]]:
    items = [
        item for item in tools.list_skills(permissions)
        if all(app_scopes.tool_allowed(scope, key) for key in item.get("tools") or [])
    ]
    return [{
        "key": str(item.get("key") or "")[:80],
        "name": str(item.get("name") or "")[:160],
        "available": bool(item.get("available")),
        "tools": [str(value)[:80] for value in item.get("tools", []) if isinstance(value, str)][:50],
    } for item in items[:100]]


def _plugin_inventory(scope: dict[str, Any], permissions: set[str]) -> list[dict[str, Any]]:
    if "plugins.read" not in permissions:
        return []
    try:
        items = plugins.list_plugins(active_only=True)
    except Exception:
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("plugin_key") or "")
        if not app_scopes.plugin_allowed(scope, key):
            continue
        manifest = item.get("manifest") if isinstance(item.get("manifest"), dict) else {}
        result.append({
            "key": key[:80],
            "name": str(item.get("name") or key)[:160],
            "version": str(item.get("version") or "")[:80],
            "trusted": bool(item.get("trusted")),
            "tool_keys": [str(tool.get("key") or "")[:80] for tool in manifest.get("tools", []) if isinstance(tool, dict)][:50],
        })
        if len(result) >= 100:
            break
    return result


def _services(inference: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        remote = bridge_status().get("runtime", {})
    except Exception:
        remote = {}
    return [
        {"key": "homeserver", "available": True, "status": "running", "version": settings.version},
        {"key": "inference", "available": bool(inference.get("available")), "status": "ready" if inference.get("available") else "not_configured"},
        {"key": "remote_bridge", "available": bool(remote.get("running")), "status": "connected" if remote.get("connected") else str(remote.get("stage") or "stopped")[:40]},
        {"key": "knowledge_index", "available": True, "status": "ready"},
    ]


def _operations(permissions: set[str]) -> list[str]:
    operations = ["capability.registry"]
    mapping = {
        "agent.chat": ["agent.chat", "inference.status", "conversations.list", "conversation.get"],
        "contacts.read": ["contacts.search"],
        "knowledge.search": ["knowledge.search"],
        "memory.read": ["memory.read"],
        "memory.write": ["memory.write"],
        "events.read": ["events.list"],
        "events.write": ["events.emit"],
        "awareness.read": ["awareness.list"],
        "plugins.read": ["plugins.list"],
        "usage.read": ["usage.read"],
        "usage.write": ["usage.write"],
        "tools.execute": ["tools.list", "skills.list", "tool.execute"],
        "approvals.review": ["action.status", "action.list", "action.approve", "action.deny"],
    }
    for permission, values in mapping.items():
        if permission in permissions:
            operations.extend(values)
    return sorted(set(operations))


def build_registry(identity: dict[str, Any]) -> dict[str, Any]:
    permissions = {str(value) for value in identity.get("permissions", []) if isinstance(value, str)}
    scope = app_scopes.normalize(identity.get("scope") if isinstance(identity.get("scope"), dict) else None)
    inference = _inference_inventory()
    tools_inventory = _tool_inventory(scope, permissions)
    skills_inventory = _skill_inventory(scope, permissions)
    plugins_inventory = _plugin_inventory(scope, permissions)
    return {
        "registry_version": REGISTRY_VERSION,
        "service": settings.app_name,
        "version": settings.version,
        "app": {
            "key": str(identity.get("app_key") or "")[:80],
            "name": str(identity.get("name") or "")[:120],
            "permissions": sorted(permissions),
            "scope": scope,
        },
        "brain": _primary_brain(),
        "compute": inference,
        "memory": _memory_inventory(scope, permissions),
        "knowledge": _knowledge_inventory(scope, permissions),
        "files": _file_inventory(permissions),
        "contacts": _contact_inventory(permissions),
        "tools": tools_inventory,
        "skills": skills_inventory,
        "plugins": plugins_inventory,
        "services": _services(inference),
        "operations": _operations(permissions),
        "counts": {
            "tools": len(tools_inventory),
            "available_tools": sum(1 for item in tools_inventory if item["available"]),
            "skills": len(skills_inventory),
            "available_skills": sum(1 for item in skills_inventory if item["available"]),
            "plugins": len(plugins_inventory),
            "local_models": len(inference.get("installed_local_models", [])),
        },
    }

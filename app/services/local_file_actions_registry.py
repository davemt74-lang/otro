from __future__ import annotations

from typing import Any, Callable

from . import action_policy, app_scopes, capability_registry


FILE_ACTION_VERSION = "v0.39"
FILE_ACTIONS = ("files.update", "files.delete")


def install() -> None:
    if getattr(capability_registry, "_local_file_actions_v039_installed", False):
        return

    original: Callable[[dict[str, Any]], dict[str, Any]] = capability_registry.build_registry

    def build_registry(identity: dict[str, Any]) -> dict[str, Any]:
        result = original(identity)
        permissions = {str(value) for value in identity.get("permissions", []) if isinstance(value, str)}
        scope = app_scopes.normalize(
            identity.get("scope") if isinstance(identity.get("scope"), dict) else None
        )
        app_id = int(identity.get("id") or 0)
        app_key = str(identity.get("app_key") or "")
        tool_inventory = {
            str(item.get("key") or ""): item
            for item in result.get("tools", [])
            if isinstance(item, dict)
        }

        allowed_actions: list[str] = []
        if "files.write" in permissions and "tools.execute" in permissions:
            for tool_key in FILE_ACTIONS:
                item = tool_inventory.get(tool_key)
                if not item or not bool(item.get("enabled")) or not bool(item.get("available")):
                    continue
                if not app_scopes.tool_allowed(scope, tool_key):
                    continue
                if app_id > 0 and app_key:
                    policy = action_policy.resolve_policy(app_id, app_key, tool_key)
                    if policy["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT:
                        continue
                allowed_actions.append(tool_key)

        files = dict(result.get("files") or {})
        files.update(
            {
                "action_version": FILE_ACTION_VERSION,
                "writable": bool(allowed_actions),
                "write_policy_gated": True,
                "write_actions": allowed_actions,
                "arbitrary_paths": False,
            }
        )
        result["files"] = files
        operations = set(result.get("operations") or [])
        operations.difference_update(FILE_ACTIONS)
        operations.update(allowed_actions)
        result["operations"] = sorted(operations)
        return result

    capability_registry.build_registry = build_registry

    # capability_registry_api imports build_registry by name, so update that
    # already-loaded binding as well when the runtime bootstrap installs v0.39.
    try:
        from .. import capability_registry_api
        capability_registry_api.build_registry = build_registry
    except Exception:
        pass

    capability_registry._local_file_actions_v039_installed = True
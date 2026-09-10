from __future__ import annotations

from typing import Any, Callable

from . import capability_registry, pairing


FILE_ACTION_VERSION = "v0.39"


def install() -> None:
    if getattr(capability_registry, "_local_file_actions_v039_installed", False):
        return

    pairing.DEFAULT_PERMISSIONS.add("files.write")
    original: Callable[[dict[str, Any]], dict[str, Any]] = capability_registry.build_registry

    def build_registry(identity: dict[str, Any]) -> dict[str, Any]:
        result = original(identity)
        permissions = {str(value) for value in identity.get("permissions", []) if isinstance(value, str)}
        files = dict(result.get("files") or {})
        files.update(
            {
                "action_version": FILE_ACTION_VERSION,
                "writable": "files.write" in permissions and "tools.execute" in permissions,
                "write_policy_gated": True,
                "write_actions": ["files.update", "files.delete"],
                "arbitrary_paths": False,
            }
        )
        result["files"] = files
        if files["writable"]:
            operations = set(result.get("operations") or [])
            operations.update({"files.update", "files.delete"})
            result["operations"] = sorted(operations)
        return result

    capability_registry.build_registry = build_registry
    capability_registry._local_file_actions_v039_installed = True

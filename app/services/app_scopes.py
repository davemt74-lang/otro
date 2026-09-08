from __future__ import annotations

import json
import re
from typing import Any

from ..database import db

DEFAULT_SCOPE = {
    "cloud_allowed": True,
    "memory_key_prefixes": [],
    "knowledge_kinds": [],
    "tool_names": [],
    "plugin_keys": [],
}

LOCKED_SCOPE = {
    "cloud_allowed": False,
    "memory_key_prefixes": ["__locked__:"],
    "knowledge_kinds": ["__locked__"],
    "tool_names": ["__locked__"],
    "plugin_keys": ["__locked__"],
}

PLUGIN_SCOPE_SENTINEL = "__app_scope_plugins_restricted__"
PLUGIN_SCOPE_PREFIX = "__app_scope_plugin__:"

_TOOL_PERMISSION_BY_KEY = {
    "contacts.search": "contacts.read",
    "knowledge.search": "knowledge.search",
    "memory.list": "memory.read",
    "memory.write": "memory.write",
    "notifications.list": "notifications.read",
    "tasks.list": "tasks.read",
    "tasks.create": "tasks.write",
}

_MAX_ITEMS = 32
_MAX_VALUE_LENGTH = 160
_SAFE_KIND = re.compile(r"^[A-Za-z0-9_.:-]+$")


class ScopeError(RuntimeError):
    pass


def _list(value: Any, *, allow_prefix: bool = False) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for raw in value[:_MAX_ITEMS]:
        item = str(raw or "").strip()[:_MAX_VALUE_LENGTH]
        if not item or item in output:
            continue
        if not allow_prefix and not _SAFE_KIND.fullmatch(item):
            raise ScopeError(f"Invalid scope value: {item}")
        output.append(item)
    return output


def normalize(scope: dict[str, Any] | None) -> dict[str, Any]:
    raw = scope if isinstance(scope, dict) else {}
    return {
        "cloud_allowed": bool(raw.get("cloud_allowed", True)),
        "memory_key_prefixes": _list(raw.get("memory_key_prefixes"), allow_prefix=True),
        "knowledge_kinds": _list(raw.get("knowledge_kinds")),
        "tool_names": _list(raw.get("tool_names")),
        "plugin_keys": _list(raw.get("plugin_keys")),
    }


def _decode_list(value: Any) -> list[str]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return _list(decoded, allow_prefix=True)


def get_scope(paired_app_id: int) -> dict[str, Any]:
    if paired_app_id < 1:
        return dict(DEFAULT_SCOPE)
    with db() as connection:
        row = connection.execute(
            """
            SELECT cloud_allowed, memory_key_prefixes, knowledge_kinds, tool_names, plugin_keys
            FROM app_capability_scopes
            WHERE paired_app_id=? LIMIT 1
            """,
            (paired_app_id,),
        ).fetchone()
    if row is None:
        return dict(DEFAULT_SCOPE)
    return normalize({
        "cloud_allowed": bool(row["cloud_allowed"]),
        "memory_key_prefixes": _decode_list(row["memory_key_prefixes"]),
        "knowledge_kinds": _decode_list(row["knowledge_kinds"]),
        "tool_names": _decode_list(row["tool_names"]),
        "plugin_keys": _decode_list(row["plugin_keys"]),
    })


def get_scope_for_source(source_app_key: str) -> dict[str, Any]:
    source = str(source_app_key or "").strip()
    if not source.startswith("app:"):
        return dict(DEFAULT_SCOPE)
    app_key = source[4:].strip()
    if not app_key:
        return dict(LOCKED_SCOPE)
    with db() as connection:
        row = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key=? LIMIT 1",
            (app_key,),
        ).fetchone()
    if row is None:
        return dict(LOCKED_SCOPE)
    return get_scope(int(row["id"]))


def save_scope(paired_app_id: int, scope: dict[str, Any]) -> dict[str, Any]:
    if paired_app_id < 1:
        raise ScopeError("Connected app not found")
    normalized = normalize(scope)
    with db() as connection:
        exists = connection.execute("SELECT id FROM paired_apps WHERE id=?", (paired_app_id,)).fetchone()
        if exists is None:
            raise ScopeError("Connected app not found")
        connection.execute(
            """
            INSERT INTO app_capability_scopes(
                paired_app_id, cloud_allowed, memory_key_prefixes, knowledge_kinds, tool_names, plugin_keys
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(paired_app_id) DO UPDATE SET
                cloud_allowed=excluded.cloud_allowed,
                memory_key_prefixes=excluded.memory_key_prefixes,
                knowledge_kinds=excluded.knowledge_kinds,
                tool_names=excluded.tool_names,
                plugin_keys=excluded.plugin_keys,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                paired_app_id,
                1 if normalized["cloud_allowed"] else 0,
                json.dumps(normalized["memory_key_prefixes"], separators=(",", ":")),
                json.dumps(normalized["knowledge_kinds"], separators=(",", ":")),
                json.dumps(normalized["tool_names"], separators=(",", ":")),
                json.dumps(normalized["plugin_keys"], separators=(",", ":")),
            ),
        )
    return normalized


def memory_key_allowed(scope: dict[str, Any], memory_key: str | None) -> bool:
    prefixes = normalize(scope)["memory_key_prefixes"]
    if not prefixes:
        return True
    key = str(memory_key or "")
    return any(key.startswith(prefix) for prefix in prefixes)


def knowledge_kind_allowed(scope: dict[str, Any], kind: str | None) -> bool:
    kinds = normalize(scope)["knowledge_kinds"]
    return not kinds or str(kind or "") in kinds


def tool_allowed(scope: dict[str, Any], tool_name: str | None) -> bool:
    names = normalize(scope)["tool_names"]
    return not names or str(tool_name or "") in names


def plugin_allowed(scope: dict[str, Any], plugin_key: str | None) -> bool:
    keys = normalize(scope)["plugin_keys"]
    return not keys or str(plugin_key or "") in keys


def scoped_tool_permissions(scope: dict[str, Any], permissions: set[str] | None) -> set[str]:
    """Return the model-facing permission view after app scope narrowing.

    Direct APIs continue to use the app's coarse permissions plus explicit
    resource filtering. Model tools are stricter: when a resource has a
    sub-scope that the legacy tool itself cannot enforce, that tool permission
    is withheld rather than risking broader private-data access.
    """
    normalized = normalize(scope)
    result = set(permissions or set())

    allowed_tools = set(normalized["tool_names"])
    if allowed_tools:
        for tool_key, permission in _TOOL_PERMISSION_BY_KEY.items():
            if tool_key not in allowed_tools:
                result.discard(permission)

    if normalized["memory_key_prefixes"]:
        result.discard("memory.read")
        result.discard("memory.write")
    if normalized["knowledge_kinds"]:
        result.discard("knowledge.search")

    plugin_keys = normalized["plugin_keys"]
    if plugin_keys:
        result.add(PLUGIN_SCOPE_SENTINEL)
        result.update(f"{PLUGIN_SCOPE_PREFIX}{key}" for key in plugin_keys)

    return result

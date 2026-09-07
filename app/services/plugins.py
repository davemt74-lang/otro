from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Callable
from typing import Any

from ..database import db

_PLUGIN_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
_EVENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._*:-]{0,159}$")
_TOOL_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
_HANDLER_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")

EventHandler = Callable[[dict[str, Any]], dict[str, Any] | None]
ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
_EVENT_HANDLERS: dict[tuple[str, str], EventHandler] = {}
_TOOL_HANDLERS: dict[tuple[str, str], ToolHandler] = {}


class PluginError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _bounded_text(value: Any, maximum: int, label: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PluginError(f"{label} is required.")
    if len(text) > maximum:
        raise PluginError(f"{label} exceeds {maximum} characters.")
    return text


def _validate_permissions(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise PluginError("requested_permissions must be an array.")
    result: list[str] = []
    for value in values[:100]:
        permission = _bounded_text(value, 120, "permission")
        if permission and permission not in result:
            result.append(permission)
    return sorted(result)


def _validate_tools(values: Any) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise PluginError("tools must be an array.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values[:50]:
        if not isinstance(raw, dict):
            raise PluginError("Each plugin tool must be an object.")
        key = _bounded_text(raw.get("key"), 80, "tool key", required=True).lower()
        if not _TOOL_KEY.fullmatch(key):
            raise PluginError(f"Invalid plugin tool key: {key}")
        if key in seen:
            raise PluginError(f"Duplicate plugin tool key: {key}")
        seen.add(key)
        mode = _bounded_text(raw.get("mode") or "read", 20, "tool mode").lower()
        if mode != "read":
            raise PluginError("v0.17 plugin tools are read-only. Write capabilities must use the approval system.")
        schema = raw.get("input_schema") or {"type": "object", "properties": {}, "additionalProperties": False}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise PluginError(f"Plugin tool {key} requires an object input_schema.")
        encoded = json.dumps(schema, separators=(",", ":"))
        if len(encoded) > 20000:
            raise PluginError(f"Plugin tool {key} input_schema is too large.")
        handler_key = _bounded_text(raw.get("handler_key") or key, 80, "handler key", required=True).lower()
        if not _HANDLER_KEY.fullmatch(handler_key):
            raise PluginError(f"Invalid plugin handler key: {handler_key}")
        result.append(
            {
                "key": key,
                "name": _bounded_text(raw.get("name") or key, 160, "tool name", required=True),
                "description": _bounded_text(raw.get("description") or "", 1000, "tool description"),
                "mode": "read",
                "required_permissions": _validate_permissions(raw.get("required_permissions")),
                "input_schema": schema,
                "handler_key": handler_key,
            }
        )
    return result


def validate_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PluginError("Plugin manifest must be an object.")
    plugin_key = _bounded_text(raw.get("plugin_key"), 80, "plugin_key", required=True).lower()
    if not _PLUGIN_KEY.fullmatch(plugin_key):
        raise PluginError("plugin_key may contain lowercase letters, digits, periods, underscores and hyphens.")
    name = _bounded_text(raw.get("name"), 160, "name", required=True)
    version = _bounded_text(raw.get("version"), 80, "version", required=True)
    description = _bounded_text(raw.get("description") or "", 2000, "description")

    produced: list[str] = []
    for value in raw.get("produces_events") or []:
        event_type = _bounded_text(value, 160, "event type", required=True)
        if not _EVENT_PATTERN.fullmatch(event_type) or "*" in event_type:
            raise PluginError(f"Invalid produced event type: {event_type}")
        if event_type not in produced:
            produced.append(event_type)
        if len(produced) >= 100:
            break

    subscriptions: list[dict[str, str]] = []
    for item in raw.get("subscriptions") or []:
        if not isinstance(item, dict):
            raise PluginError("Each plugin subscription must be an object.")
        pattern = _bounded_text(item.get("event_pattern"), 160, "event_pattern", required=True)
        handler_key = _bounded_text(item.get("handler_key"), 80, "handler_key", required=True).lower()
        if not _EVENT_PATTERN.fullmatch(pattern):
            raise PluginError(f"Invalid event subscription pattern: {pattern}")
        if not _HANDLER_KEY.fullmatch(handler_key):
            raise PluginError(f"Invalid plugin handler key: {handler_key}")
        subscriptions.append({"event_pattern": pattern, "handler_key": handler_key})
        if len(subscriptions) >= 100:
            break

    manifest = {
        "manifest_version": 1,
        "plugin_key": plugin_key,
        "name": name,
        "version": version,
        "description": description,
        "requested_permissions": _validate_permissions(raw.get("requested_permissions")),
        "produces_events": produced,
        "subscriptions": subscriptions,
        "tools": _validate_tools(raw.get("tools")),
        "ui_views": raw.get("ui_views") if isinstance(raw.get("ui_views"), list) else [],
        "background_jobs": raw.get("background_jobs") if isinstance(raw.get("background_jobs"), list) else [],
    }
    encoded = json.dumps(manifest, separators=(",", ":"))
    if len(encoded) > 100000:
        raise PluginError("Plugin manifest exceeds 100 KB.")
    return manifest


def register_plugin(raw_manifest: dict[str, Any], *, trusted: bool = False) -> dict[str, Any]:
    manifest = validate_manifest(raw_manifest)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO plugins(plugin_key, name, version, description, trusted, manifest_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plugin_key) DO UPDATE SET
                name=excluded.name,
                version=excluded.version,
                description=excluded.description,
                trusted=excluded.trusted,
                manifest_json=excluded.manifest_json,
                status='active',
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                manifest["plugin_key"], manifest["name"], manifest["version"],
                manifest["description"], int(bool(trusted)),
                json.dumps(manifest, separators=(",", ":")),
            ),
        )
        row = connection.execute("SELECT id FROM plugins WHERE plugin_key=?", (manifest["plugin_key"],)).fetchone()
        plugin_id = int(row["id"])
        connection.execute("DELETE FROM plugin_event_subscriptions WHERE plugin_id=?", (plugin_id,))
        for item in manifest["subscriptions"]:
            connection.execute(
                "INSERT INTO plugin_event_subscriptions(plugin_id, event_pattern, handler_key) VALUES (?, ?, ?)",
                (plugin_id, item["event_pattern"], item["handler_key"]),
            )
    return get_plugin(manifest["plugin_key"])


def _decode_manifest(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_plugin(plugin_key: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT id, plugin_key, name, version, description, status, trusted, manifest_json, installed_at, updated_at FROM plugins WHERE plugin_key=? LIMIT 1",
            (str(plugin_key).strip().lower(),),
        ).fetchone()
    if row is None:
        raise PluginError("Plugin not found.", 404)
    item = dict(row)
    item["trusted"] = bool(item["trusted"])
    item["manifest"] = _decode_manifest(item.pop("manifest_json", "{}"))
    return item


def list_plugins(*, active_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT id, plugin_key, name, version, description, status, trusted, manifest_json, installed_at, updated_at FROM plugins"
    params: tuple[Any, ...] = ()
    if active_only:
        query += " WHERE status='active'"
    query += " ORDER BY plugin_key"
    with db() as connection:
        rows = connection.execute(query, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["trusted"] = bool(item["trusted"])
        item["manifest"] = _decode_manifest(item.pop("manifest_json", "{}"))
        result.append(item)
    return result


def set_plugin_status(plugin_key: str, status: str) -> dict[str, Any]:
    state = str(status).strip().lower()
    if state not in {"active", "paused", "revoked"}:
        raise PluginError("Plugin status must be active, paused or revoked.")
    with db() as connection:
        cursor = connection.execute(
            "UPDATE plugins SET status=?, updated_at=CURRENT_TIMESTAMP WHERE plugin_key=?",
            (state, str(plugin_key).strip().lower()),
        )
        if cursor.rowcount == 0:
            raise PluginError("Plugin not found.", 404)
    return get_plugin(plugin_key)


def register_event_handler(plugin_key: str, handler_key: str, handler: EventHandler) -> None:
    if not callable(handler):
        raise PluginError("Plugin event handler must be callable.")
    _EVENT_HANDLERS[(plugin_key.strip().lower(), handler_key.strip().lower())] = handler


def register_tool_handler(plugin_key: str, handler_key: str, handler: ToolHandler) -> None:
    if not callable(handler):
        raise PluginError("Plugin tool handler must be callable.")
    _TOOL_HANDLERS[(plugin_key.strip().lower(), handler_key.strip().lower())] = handler


def dispatch_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    with db() as connection:
        rows = connection.execute(
            """
            SELECT p.plugin_key, s.event_pattern, s.handler_key
            FROM plugin_event_subscriptions s
            JOIN plugins p ON p.id=s.plugin_id
            WHERE p.status='active' AND s.enabled=1
            ORDER BY p.plugin_key, s.id
            """
        ).fetchall()
    deliveries: list[dict[str, Any]] = []
    for row in rows:
        if not fnmatch.fnmatchcase(event_type, row["event_pattern"]):
            continue
        key = (str(row["plugin_key"]), str(row["handler_key"]))
        handler = _EVENT_HANDLERS.get(key)
        if handler is None:
            deliveries.append({"plugin_key": key[0], "handler_key": key[1], "status": "unbound"})
            continue
        try:
            result = handler(dict(event)) or {}
            deliveries.append({"plugin_key": key[0], "handler_key": key[1], "status": "completed", "result": result})
        except Exception as exc:
            deliveries.append({"plugin_key": key[0], "handler_key": key[1], "status": "failed", "error": str(exc)[:500]})
    return {"deliveries": deliveries, "count": len(deliveries)}


def _model_tool_name(plugin_key: str, tool_key: str) -> str:
    clean_plugin = re.sub(r"[^a-zA-Z0-9_]", "_", plugin_key)
    clean_tool = re.sub(r"[^a-zA-Z0-9_]", "_", tool_key)
    return f"plugin__{clean_plugin}__{clean_tool}"[:240]


def available_model_tools(granted_permissions: set[str] | None = None, *, owner: bool = False) -> list[dict[str, Any]]:
    granted = set(granted_permissions or set())
    result: list[dict[str, Any]] = []
    for plugin in list_plugins(active_only=True):
        manifest = plugin.get("manifest") or {}
        for tool in manifest.get("tools") or []:
            handler_key = str(tool.get("handler_key") or tool.get("key") or "")
            bound = (plugin["plugin_key"], handler_key) in _TOOL_HANDLERS
            required = set(tool.get("required_permissions") or [])
            if not owner:
                required.add("tools.execute")
            available = bool(bound and (owner or required.issubset(granted)))
            result.append(
                {
                    **tool,
                    "plugin_key": plugin["plugin_key"],
                    "model_name": _model_tool_name(plugin["plugin_key"], tool["key"]),
                    "bound": bound,
                    "available": available,
                    "missing_permissions": sorted(required - granted) if not owner else [],
                }
            )
    return result


def execute_model_tool(
    model_name: str,
    arguments: dict[str, Any],
    granted_permissions: set[str] | None = None,
    *,
    owner: bool = False,
    source_app_key: str,
) -> dict[str, Any]:
    tool = next((item for item in available_model_tools(granted_permissions, owner=owner) if item["model_name"] == model_name), None)
    if tool is None or not tool.get("available"):
        raise PluginError("Plugin tool is not available to this conversation.", 403)
    handler = _TOOL_HANDLERS.get((tool["plugin_key"], tool["handler_key"]))
    if handler is None:
        raise PluginError("Plugin tool handler is not loaded.", 503)
    context = {"source_app_key": source_app_key, "plugin_key": tool["plugin_key"], "tool_key": tool["key"], "owner": owner}
    result = handler(dict(arguments or {}), context)
    if not isinstance(result, dict):
        raise PluginError("Plugin tool handler returned an invalid result.", 500)
    return {"plugin_key": tool["plugin_key"], "tool_key": tool["key"], "result": result}

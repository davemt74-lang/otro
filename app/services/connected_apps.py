from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import app_scopes
from .pairing import DEFAULT_PERMISSIONS


class ConnectedAppError(RuntimeError):
    pass


def _json_list(value: Any) -> list[str]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    output: list[str] = []
    for raw in decoded:
        item = str(raw or "").strip()
        if item and item not in output:
            output.append(item)
    return output


def scope_summary(scope: dict[str, Any] | None) -> dict[str, Any]:
    normalized = app_scopes.normalize(scope)
    return {
        "cloud_allowed": bool(normalized["cloud_allowed"]),
        "memory_restricted": bool(normalized["memory_key_prefixes"]),
        "memory_prefix_count": len(normalized["memory_key_prefixes"]),
        "knowledge_restricted": bool(normalized["knowledge_kinds"]),
        "knowledge_kind_count": len(normalized["knowledge_kinds"]),
        "tools_restricted": bool(normalized["tool_names"]),
        "tool_count": len(normalized["tool_names"]),
        "plugins_restricted": bool(normalized["plugin_keys"]),
        "plugin_count": len(normalized["plugin_keys"]),
    }


def _app_activity(connection, app_id: int, app_key: str, limit: int) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    rows = connection.execute(
        """
        SELECT actor_type, actor_key, action, resource_type, created_at
        FROM activity_log
        WHERE (actor_type='app' AND actor_key=?)
           OR (actor_type='owner' AND resource_type='app' AND resource_key=?)
           OR (actor_type='system' AND resource_type='app' AND resource_key=?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (app_key, str(app_id), app_key, bounded),
    ).fetchall()
    return [dict(row) for row in rows]


def list_connected_apps() -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute(
            "SELECT id, app_key, name, status, paired_at, last_seen_at FROM paired_apps ORDER BY id DESC"
        ).fetchall()
        apps: list[dict[str, Any]] = []
        allowed_by_key: dict[str, set[str]] = {}
        for row in rows:
            app_id = int(row["id"])
            permission_rows = connection.execute(
                "SELECT permission, allowed FROM app_permissions WHERE paired_app_id=? ORDER BY permission",
                (app_id,),
            ).fetchall()
            permissions = [dict(item) for item in permission_rows]
            allowed = {str(item["permission"]) for item in permission_rows if bool(item["allowed"])}
            allowed_by_key[str(row["app_key"])] = allowed
            scope = app_scopes.get_scope(app_id)
            item = dict(row)
            item["permissions"] = permissions
            item["allowed_permission_count"] = len(allowed)
            item["scope"] = scope
            item["scope_summary"] = scope_summary(scope)
            item["recent_activity"] = _app_activity(connection, app_id, str(row["app_key"]), 4)
            apps.append(item)

        pending_rows = connection.execute(
            """
            SELECT id, app_key, app_name, requested_permissions, status, expires_at, created_at
            FROM pairing_requests
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    pending: list[dict[str, Any]] = []
    existing_keys = {str(item["app_key"]) for item in apps}
    for row in pending_rows:
        item = dict(row)
        requested = [p for p in _json_list(item.pop("requested_permissions", "[]")) if p in DEFAULT_PERMISSIONS]
        current = allowed_by_key.get(str(item["app_key"]), set())
        new_permissions = sorted(set(requested) - current)
        item["requested_permissions"] = requested
        item["existing_app"] = str(item["app_key"]) in existing_keys
        item["new_permissions"] = new_permissions
        item["requests_additional_capabilities"] = bool(item["existing_app"] and new_permissions)
        pending.append(item)

    counts = {
        "total": len(apps),
        "active": sum(1 for item in apps if item["status"] == "active"),
        "paused": sum(1 for item in apps if item["status"] == "paused"),
        "revoked": sum(1 for item in apps if item["status"] == "revoked"),
        "pending": len(pending),
    }
    return {
        "apps": apps,
        "pending": pending,
        "counts": counts,
        "available_permissions": sorted(DEFAULT_PERMISSIONS),
        "pairing_protocol": "claim-v1",
    }


def connected_app_activity(app_id: int, limit: int = 50) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT id, app_key, name FROM paired_apps WHERE id=? LIMIT 1",
            (app_id,),
        ).fetchone()
        if row is None:
            raise ConnectedAppError("Connected app not found")
        items = _app_activity(connection, int(row["id"]), str(row["app_key"]), limit)
    return {
        "app": {"id": int(row["id"]), "app_key": str(row["app_key"]), "name": str(row["name"])},
        "items": items,
    }


def require_repair(app_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT id, app_key, name, status FROM paired_apps WHERE id=? LIMIT 1",
            (app_id,),
        ).fetchone()
        if row is None:
            raise ConnectedAppError("Connected app not found")
        connection.execute(
            "UPDATE paired_apps SET status='revoked' WHERE id=?",
            (app_id,),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'app.repair_required', 'app', ?, ?)
            """,
            (
                str(app_id),
                json.dumps({"app_key": str(row["app_key"]), "previous_status": str(row["status"])}, separators=(",", ":")),
            ),
        )
    return {
        "updated": True,
        "status": "revoked",
        "app_key": str(row["app_key"]),
        "message": "Existing credentials were revoked. Pair this application again to issue a replacement credential; existing resource scopes are preserved.",
    }


def deny_pairing_request(request_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT id, app_key, app_name, status FROM pairing_requests WHERE id=? LIMIT 1",
            (request_id,),
        ).fetchone()
        if row is None:
            raise ConnectedAppError("Pairing request not found")
        if str(row["status"]) != "pending":
            raise ConnectedAppError("Pairing request is no longer pending")
        connection.execute(
            "UPDATE pairing_requests SET status='denied' WHERE id=?",
            (request_id,),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'pairing.denied', 'app', ?, ?)
            """,
            (
                str(row["app_key"]),
                json.dumps({"request_id": int(row["id"]), "app_name": str(row["app_name"])}, separators=(",", ":")),
            ),
        )
    return {"updated": True, "status": "denied", "app_key": str(row["app_key"])}

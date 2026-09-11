from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import tools

READ_ONLY = "read_only"
SAFE_AUTOMATIC = "safe_automatic"
APPROVAL_REQUIRED = "approval_required"
SENSITIVE_HIGH_IMPACT = "sensitive_high_impact"
VALID_MODES = {READ_ONLY, SAFE_AUTOMATIC, APPROVAL_REQUIRED, SENSITIVE_HIGH_IMPACT}
APPROVAL_ONLY_WRITE_TOOLS = {
    "files.update",
    "files.delete",
    "vp3.booking.create",
    "vp3.booking.reschedule",
    "vp3.booking.cancel",
}


class ActionPolicyError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _tool(tool_key: str) -> dict[str, Any]:
    item = tools.TOOL_DEFINITIONS.get(str(tool_key or "").strip())
    if item is None:
        raise ActionPolicyError("Tool not found.", 404)
    return item


def default_mode(tool_key: str) -> str:
    return READ_ONLY if str(_tool(tool_key).get("mode") or "") == "read" else APPROVAL_REQUIRED


def allowed_modes(tool_key: str) -> list[str]:
    mode = str(_tool(tool_key).get("mode") or "")
    if mode == "read":
        return [READ_ONLY, SENSITIVE_HIGH_IMPACT]
    if tool_key in APPROVAL_ONLY_WRITE_TOOLS:
        return [APPROVAL_REQUIRED, SENSITIVE_HIGH_IMPACT]
    return [SAFE_AUTOMATIC, APPROVAL_REQUIRED, SENSITIVE_HIGH_IMPACT]


def resolve_policy(app_id: int, app_key: str, tool_key: str) -> dict[str, Any]:
    tool = _tool(tool_key)
    with db() as connection:
        row = connection.execute(
            "SELECT policy_mode, updated_at FROM app_tool_execution_policies WHERE paired_app_id=? AND tool_key=? LIMIT 1",
            (int(app_id), tool_key),
        ).fetchone()
    inherited = row is None
    allowed = allowed_modes(tool_key)
    stored_mode = None if row is None else str(row["policy_mode"])
    invalid_override_ignored = stored_mode is not None and stored_mode not in allowed
    policy_mode = default_mode(tool_key) if inherited or invalid_override_ignored else str(stored_mode)
    return {
        "app_id": int(app_id),
        "app_key": str(app_key),
        "tool_key": tool_key,
        "tool_name": str(tool.get("name") or tool_key),
        "tool_mode": str(tool.get("mode") or ""),
        "policy_mode": policy_mode,
        "inherited": inherited or invalid_override_ignored,
        "allowed_modes": allowed,
        "updated_at": None if row is None else row["updated_at"],
    }


def resolve_policy_for_source(source_app_key: str, tool_key: str) -> dict[str, Any] | None:
    source = str(source_app_key or "").strip()
    if not source.startswith("app:"):
        return None
    app_key = source[4:].strip()
    if not app_key:
        return None
    with db() as connection:
        app = connection.execute(
            "SELECT id, app_key FROM paired_apps WHERE app_key=? LIMIT 1",
            (app_key,),
        ).fetchone()
    if app is None:
        return None
    return resolve_policy(int(app["id"]), str(app["app_key"]), tool_key)


def list_policy_for_app(app_id: int, app_key: str) -> list[dict[str, Any]]:
    return [resolve_policy(app_id, app_key, key) for key in sorted(tools.TOOL_DEFINITIONS)]


def list_owner_policies() -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute(
            "SELECT id, app_key, name, status, last_seen_at FROM paired_apps ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    apps: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["policies"] = list_policy_for_app(int(row["id"]), str(row["app_key"]))
        apps.append(item)
    return {
        "version": "v0.35",
        "modes": [READ_ONLY, SAFE_AUTOMATIC, APPROVAL_REQUIRED, SENSITIVE_HIGH_IMPACT],
        "apps": apps,
    }


def set_policy(app_id: int, tool_key: str, policy_mode: str | None) -> dict[str, Any]:
    tool = _tool(tool_key)
    normalized = str(policy_mode or "inherit").strip().lower()
    with db() as connection:
        app = connection.execute(
            "SELECT id, app_key, name FROM paired_apps WHERE id=? LIMIT 1",
            (int(app_id),),
        ).fetchone()
        if app is None:
            raise ActionPolicyError("Connected app not found.", 404)

        if normalized == "inherit":
            connection.execute(
                "DELETE FROM app_tool_execution_policies WHERE paired_app_id=? AND tool_key=?",
                (int(app_id), tool_key),
            )
            action = "action.policy.inherit"
        else:
            if normalized not in VALID_MODES:
                raise ActionPolicyError("Invalid action policy mode.")
            if normalized not in allowed_modes(tool_key):
                raise ActionPolicyError(
                    f"{normalized} is not valid for this {str(tool.get('mode') or 'unknown')} tool."
                )
            connection.execute(
                """
                INSERT INTO app_tool_execution_policies(paired_app_id, tool_key, policy_mode)
                VALUES (?, ?, ?)
                ON CONFLICT(paired_app_id, tool_key) DO UPDATE SET
                    policy_mode=excluded.policy_mode,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(app_id), tool_key, normalized),
            )
            action = "action.policy.updated"

        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', ?, 'tool', ?, ?)
            """,
            (
                action,
                tool_key,
                json.dumps(
                    {"paired_app_id": int(app_id), "app_key": str(app["app_key"]), "policy_mode": normalized},
                    separators=(",", ":"),
                ),
            ),
        )
    return resolve_policy(int(app["id"]), str(app["app_key"]), tool_key)


def record_decision(
    app_id: int,
    app_key: str,
    tool_key: str,
    policy_mode: str,
    decision: str,
    *,
    request_id: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if policy_mode not in VALID_MODES:
        return
    if decision not in {"allowed_read", "allowed_automatic", "approval_requested", "blocked"}:
        return
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    with db() as connection:
        connection.execute(
            """
            INSERT INTO action_policy_decisions(
                paired_app_id, source_app_key, tool_key, policy_mode, decision,
                request_id, reason, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(app_id), str(app_key), tool_key, policy_mode, decision,
                request_id, (reason or "")[:500] or None,
                json.dumps(safe_metadata, separators=(",", ":")),
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('system', 'action-policy', ?, 'tool', ?, ?)
            """,
            (
                f"action.policy.{decision}",
                tool_key,
                json.dumps(
                    {
                        "app_key": str(app_key),
                        "policy_mode": policy_mode,
                        "request_id": request_id,
                    },
                    separators=(",", ":"),
                ),
            ),
        )


def list_audit(limit: int = 200, app_id: int | None = None) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    params: list[Any] = []
    where = ""
    if app_id is not None:
        where = "WHERE d.paired_app_id=?"
        params.append(int(app_id))
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT d.id,d.paired_app_id,d.source_app_key,d.tool_key,d.policy_mode,d.decision,
                   d.request_id,d.reason,d.metadata_json,d.created_at,p.name AS app_name
            FROM action_policy_decisions d
            LEFT JOIN paired_apps p ON p.id=d.paired_app_id
            {where}
            ORDER BY d.id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["metadata"] = {}
            item.pop("metadata_json", None)
        items.append(item)
    return items

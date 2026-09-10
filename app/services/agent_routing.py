from __future__ import annotations

import json
from typing import Any

from ..database import db

AGENT_ROUTING_VERSION = "v0.47"


class AgentRoutingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _primary_agent(connection=None) -> dict[str, Any]:
    if connection is None:
        with db() as owned:
            return _primary_agent(owned)
    row = connection.execute(
        "SELECT id, name, instructions, model, is_primary FROM agents WHERE is_primary=1 LIMIT 1"
    ).fetchone()
    if row is None:
        raise AgentRoutingError("Primary Agent is not configured.", 503)
    return dict(row)


def _agent(agent_id: int, connection=None) -> dict[str, Any]:
    if int(agent_id) < 1:
        raise AgentRoutingError("Agent not found.", 404)
    if connection is None:
        with db() as owned:
            return _agent(agent_id, owned)
    row = connection.execute(
        "SELECT id, name, instructions, model, is_primary FROM agents WHERE id=? LIMIT 1",
        (int(agent_id),),
    ).fetchone()
    if row is None:
        raise AgentRoutingError("Agent not found.", 404)
    return dict(row)


def _paired_app(source_app_key: str, connection=None) -> dict[str, Any]:
    source = str(source_app_key or "").strip()
    if not source.startswith("app:") or not source[4:].strip():
        raise AgentRoutingError("Application identity is invalid.", 403)
    app_key = source[4:].strip()
    if connection is None:
        with db() as owned:
            return _paired_app(source, owned)
    row = connection.execute(
        "SELECT id, app_key, name, status FROM paired_apps WHERE app_key=? LIMIT 1",
        (app_key,),
    ).fetchone()
    if row is None or str(row["status"]) != "active":
        raise AgentRoutingError("Application is not authorized for Agent routing.", 403)
    return dict(row)


def safe_summary(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(agent["id"]),
        "name": str(agent.get("name") or "Agent"),
        "is_primary": bool(agent.get("is_primary")),
    }


def resolve_agent(
    source_app_key: str,
    requested_agent_id: int | None = None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    with db() as connection:
        agent = _primary_agent(connection) if requested_agent_id is None else _agent(int(requested_agent_id), connection)
        if owner:
            return agent
        app = _paired_app(source_app_key, connection)
        if bool(agent["is_primary"]):
            return agent
        grant = connection.execute(
            """
            SELECT allowed FROM app_agent_grants
            WHERE paired_app_id=? AND agent_id=? LIMIT 1
            """,
            (int(app["id"]), int(agent["id"])),
        ).fetchone()
        if grant is None or not bool(grant["allowed"]):
            raise AgentRoutingError("Agent is not authorized for this application.", 403)
        return agent


def selectable_agents(source_app_key: str, *, owner: bool = False) -> dict[str, Any]:
    with db() as connection:
        if owner:
            rows = connection.execute(
                "SELECT id, name, is_primary FROM agents ORDER BY is_primary DESC, lower(name), id"
            ).fetchall()
        else:
            app = _paired_app(source_app_key, connection)
            rows = connection.execute(
                """
                SELECT a.id, a.name, a.is_primary
                FROM agents a
                LEFT JOIN app_agent_grants g
                  ON g.agent_id=a.id AND g.paired_app_id=? AND g.allowed=1
                WHERE a.is_primary=1 OR g.agent_id IS NOT NULL
                ORDER BY a.is_primary DESC, lower(a.name), a.id
                """,
                (int(app["id"]),),
            ).fetchall()
    items = [safe_summary(dict(row)) for row in rows]
    return {
        "version": AGENT_ROUTING_VERSION,
        "primary_implicit": True,
        "items": items,
    }


def app_agent_access(paired_app_id: int) -> dict[str, Any]:
    with db() as connection:
        app = connection.execute(
            "SELECT id, app_key, name, status FROM paired_apps WHERE id=? LIMIT 1",
            (int(paired_app_id),),
        ).fetchone()
        if app is None:
            raise AgentRoutingError("Connected app not found.", 404)
        rows = connection.execute(
            """
            SELECT a.id, a.name, a.is_primary, COALESCE(g.allowed, 0) AS allowed
            FROM agents a
            LEFT JOIN app_agent_grants g
              ON g.agent_id=a.id AND g.paired_app_id=?
            ORDER BY a.is_primary DESC, lower(a.name), a.id
            """,
            (int(paired_app_id),),
        ).fetchall()
    items = []
    for row in rows:
        item = safe_summary(dict(row))
        item["allowed"] = True if item["is_primary"] else bool(row["allowed"])
        item["implicit"] = bool(item["is_primary"])
        items.append(item)
    return {
        "version": AGENT_ROUTING_VERSION,
        "primary_implicit": True,
        "items": items,
        "secondary_allowed_count": sum(1 for item in items if item["allowed"] and not item["is_primary"]),
    }


def save_app_agent_grant(paired_app_id: int, agent_id: int, allowed: bool) -> dict[str, Any]:
    with db() as connection:
        app = connection.execute(
            "SELECT id, app_key, name FROM paired_apps WHERE id=? LIMIT 1",
            (int(paired_app_id),),
        ).fetchone()
        if app is None:
            raise AgentRoutingError("Connected app not found.", 404)
        agent = _agent(int(agent_id), connection)
        if bool(agent["is_primary"]):
            raise AgentRoutingError(
                "Primary Agent access is provided by agent.chat and cannot be changed here.",
                409,
            )
        connection.execute(
            """
            INSERT INTO app_agent_grants(paired_app_id, agent_id, allowed)
            VALUES (?, ?, ?)
            ON CONFLICT(paired_app_id, agent_id) DO UPDATE SET
                allowed=excluded.allowed,
                updated_at=CURRENT_TIMESTAMP
            """,
            (int(paired_app_id), int(agent_id), 1 if allowed else 0),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'app.agent_access.updated', 'app', ?, ?)
            """,
            (
                str(paired_app_id),
                json.dumps(
                    {
                        "app_key": str(app["app_key"]),
                        "agent_id": int(agent_id),
                        "agent_name": str(agent["name"]),
                        "allowed": bool(allowed),
                        "version": AGENT_ROUTING_VERSION,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return {
        "updated": True,
        "version": AGENT_ROUTING_VERSION,
        "app_id": int(paired_app_id),
        "agent": safe_summary(agent),
        "allowed": bool(allowed),
    }

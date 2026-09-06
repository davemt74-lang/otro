from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from ..database import db

DEFAULT_PERMISSIONS = {
    "agent.chat",
    "knowledge.search",
    "memory.read",
    "memory.write",
    "notifications.read",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_pairing_request(app_key: str, app_name: str, permissions: list[str]) -> dict:
    requested = sorted({p for p in permissions if p in DEFAULT_PERMISSIONS})
    code = "-".join((secrets.token_hex(2).upper(), secrets.token_hex(2).upper()))
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO pairing_requests(code_hash, app_key, app_name, requested_permissions, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_hash(code), app_key.strip(), app_name.strip(), json.dumps(requested), expires.isoformat()),
        )
    return {"code": code, "expires_at": expires.isoformat(), "permissions": requested}


def approve_pairing(code: str) -> dict | None:
    now = datetime.now(timezone.utc)
    with db() as connection:
        request = connection.execute(
            "SELECT * FROM pairing_requests WHERE code_hash = ? AND status = 'pending'",
            (_hash(code),),
        ).fetchone()
        if request is None:
            return None
        if datetime.fromisoformat(request["expires_at"]) <= now:
            connection.execute("UPDATE pairing_requests SET status='expired' WHERE id=?", (request["id"],))
            return None

        raw_token = secrets.token_urlsafe(48)
        token_hash = _hash(raw_token)
        connection.execute(
            """
            INSERT INTO paired_apps(app_key, name, token_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(app_key) DO UPDATE SET
                name=excluded.name,
                token_hash=excluded.token_hash,
                status='active',
                paired_at=CURRENT_TIMESTAMP
            """,
            (request["app_key"], request["app_name"], token_hash),
        )
        app = connection.execute("SELECT id FROM paired_apps WHERE app_key=?", (request["app_key"],)).fetchone()
        permissions = json.loads(request["requested_permissions"])
        for permission in permissions:
            connection.execute(
                """
                INSERT INTO app_permissions(paired_app_id, permission, allowed)
                VALUES (?, ?, 1)
                ON CONFLICT(paired_app_id, permission)
                DO UPDATE SET allowed=1, updated_at=CURRENT_TIMESTAMP
                """,
                (app["id"], permission),
            )
        connection.execute("UPDATE pairing_requests SET status='approved' WHERE id=?", (request["id"],))
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key) VALUES ('system', 'pairing', 'app.paired', 'app', ?)",
            (request["app_key"],),
        )
    return {"app_key": request["app_key"], "token": raw_token, "permissions": permissions}


def authenticate(raw_token: str) -> dict | None:
    with db() as connection:
        row = connection.execute(
            "SELECT id, app_key, name FROM paired_apps WHERE token_hash=? AND status='active'",
            (_hash(raw_token),),
        ).fetchone()
        if row is None:
            return None
        connection.execute("UPDATE paired_apps SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        permissions = connection.execute(
            "SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1",
            (row["id"],),
        ).fetchall()
    return {"id": row["id"], "app_key": row["app_key"], "name": row["name"], "permissions": [p["permission"] for p in permissions]}

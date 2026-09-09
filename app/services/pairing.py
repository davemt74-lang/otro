from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from ..database import db
from . import app_scopes

DEFAULT_PERMISSIONS = {
    "agent.chat",
    "approvals.review",
    "awareness.read",
    "contacts.read",
    "events.read",
    "events.write",
    "knowledge.search",
    "memory.read",
    "memory.write",
    "notifications.read",
    "plugins.read",
    "tasks.read",
    "tasks.write",
    "tools.execute",
    "usage.read",
    "usage.write",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=10)


def create_pairing_request(app_key: str, app_name: str, permissions: list[str]) -> dict:
    requested = sorted({p for p in permissions if p in DEFAULT_PERMISSIONS})
    code = "-".join((secrets.token_hex(2).upper(), secrets.token_hex(2).upper()))
    request_id = secrets.token_urlsafe(18)
    claim_token = secrets.token_urlsafe(48)
    expires = _expires_at()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO pairing_requests(
                code_hash,
                app_key,
                app_name,
                requested_permissions,
                expires_at,
                request_id,
                claim_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _hash(code),
                app_key.strip(),
                app_name.strip(),
                json.dumps(requested),
                expires.isoformat(),
                request_id,
                _hash(claim_token),
            ),
        )
    return {
        "protocol": "claim-v1",
        "code": code,
        "request_id": request_id,
        "claim_token": claim_token,
        "expires_at": expires.isoformat(),
        "permissions": requested,
    }


def _expire_request(connection, request) -> bool:
    if datetime.fromisoformat(request["expires_at"]) > datetime.now(timezone.utc):
        return False
    if request["status"] == "pending":
        connection.execute(
            "UPDATE pairing_requests SET status='expired' WHERE id=?",
            (request["id"],),
        )
    return True


def approve_pairing(code: str) -> dict | None:
    with db() as connection:
        request = connection.execute(
            "SELECT * FROM pairing_requests WHERE code_hash=? AND status='pending'",
            (_hash(code),),
        ).fetchone()
        if request is None:
            return None
        if _expire_request(connection, request):
            return None

        claim_hash = request["claim_hash"]
        legacy_token: str | None = None
        if claim_hash:
            token_hash = claim_hash
            delivery = "claim_token"
        else:
            legacy_token = secrets.token_urlsafe(48)
            token_hash = _hash(legacy_token)
            delivery = "legacy_token"

        connection.execute(
            """
            INSERT INTO paired_apps(app_key, name, token_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(app_key) DO UPDATE SET
                name=excluded.name,
                token_hash=excluded.token_hash,
                status='active',
                paired_at=CURRENT_TIMESTAMP,
                last_seen_at=NULL
            """,
            (request["app_key"], request["app_name"], token_hash),
        )
        app = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key=?",
            (request["app_key"],),
        ).fetchone()
        if app is None:
            raise RuntimeError("Paired application record was not created")

        # Preserve owner-defined resource scopes when an existing wrapper is
        # re-paired; a new app starts unrestricted within its granted permissions.
        connection.execute(
            "INSERT OR IGNORE INTO app_capability_scopes(paired_app_id) VALUES (?)",
            (app["id"],),
        )
        connection.execute(
            "UPDATE app_permissions SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=?",
            (app["id"],),
        )

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

        connection.execute(
            "UPDATE pairing_requests SET status='approved' WHERE id=?",
            (request["id"],),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('system', 'pairing', 'app.paired', 'app', ?, ?)
            """,
            (
                request["app_key"],
                json.dumps(
                    {"delivery": delivery, "permissions": permissions},
                    separators=(",", ":"),
                ),
            ),
        )

    result = {
        "app_key": request["app_key"],
        "permissions": permissions,
        "delivery": delivery,
    }
    if legacy_token is not None:
        result["token"] = legacy_token
    return result


def pairing_status(request_id: str, claim_token: str) -> dict | None:
    claim_hash = _hash(claim_token)
    with db() as connection:
        request = connection.execute(
            """
            SELECT *
            FROM pairing_requests
            WHERE request_id=? AND claim_hash=?
            LIMIT 1
            """,
            (request_id.strip(), claim_hash),
        ).fetchone()
        if request is None:
            return None

        expired = _expire_request(connection, request)
        status = "expired" if expired else request["status"]
        permissions = json.loads(request["requested_permissions"])

        ready = False
        if status == "approved":
            app = connection.execute(
                """
                SELECT id
                FROM paired_apps
                WHERE app_key=? AND token_hash=? AND status='active'
                LIMIT 1
                """,
                (request["app_key"], claim_hash),
            ).fetchone()
            ready = app is not None

        return {
            "protocol": "claim-v1",
            "request_id": request["request_id"],
            "app_key": request["app_key"],
            "status": status,
            "ready": ready,
            "expires_at": request["expires_at"],
            "permissions": permissions,
        }


def authenticate(raw_token: str) -> dict | None:
    with db() as connection:
        row = connection.execute(
            "SELECT id, app_key, name FROM paired_apps WHERE token_hash=? AND status='active'",
            (_hash(raw_token),),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE paired_apps SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["id"],),
        )
        permissions = connection.execute(
            """
            SELECT permission
            FROM app_permissions
            WHERE paired_app_id=? AND allowed=1
            ORDER BY permission
            """,
            (row["id"],),
        ).fetchall()
    return {
        "id": row["id"],
        "app_key": row["app_key"],
        "name": row["name"],
        "permissions": [p["permission"] for p in permissions],
        "scope": app_scopes.get_scope(int(row["id"])),
    }

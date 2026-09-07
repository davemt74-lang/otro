from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .config import settings


class RelayAuthError(RuntimeError):
    pass


class RelayClaimError(RuntimeError):
    pass


_DEVICE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected_device_id(secret: str) -> str:
    return f"hs-{_sha256(secret)[:24]}"


def _normalize_claim_code(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _new_claim_code() -> str:
    raw = "".join(secrets.choice(_DEVICE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _claim_hash(value: str) -> str:
    return _sha256(_normalize_claim_code(value))


def _new_session_token() -> str:
    return secrets.token_urlsafe(48)


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS relay_devices (
                device_id TEXT PRIMARY KEY,
                secret_hash TEXT NOT NULL,
                claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
                claim_code_hash TEXT,
                claim_expires_at TEXT,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS relay_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL REFERENCES relay_devices(device_id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                revoked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS relay_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                event TEXT NOT NULL,
                status TEXT NOT NULL,
                operation TEXT,
                request_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_relay_sessions_device
                ON relay_sessions(device_id, revoked_at);
            CREATE INDEX IF NOT EXISTS idx_relay_events_created
                ON relay_events(created_at DESC);
            """
        )


def register_or_auth_device(device_id: str, device_secret: str) -> dict:
    candidate_id = str(device_id or "").strip()
    secret = str(device_secret or "").strip()
    if len(secret) < 40 or len(secret) > 512:
        raise RelayAuthError("Invalid HomeServer device credential.")
    expected = _expected_device_id(secret)
    if not hmac.compare_digest(candidate_id, expected):
        raise RelayAuthError("HomeServer device identity does not match its credential.")

    secret_hash = _sha256(secret)
    now = _now()
    claim_code: str | None = None
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT device_id, secret_hash, claimed FROM relay_devices WHERE device_id=? LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if row is None:
            count = int(connection.execute("SELECT COUNT(*) FROM relay_devices").fetchone()[0])
            if count >= settings.max_devices:
                raise RelayAuthError("Relay device capacity has been reached.")
            connection.execute(
                """
                INSERT INTO relay_devices(device_id, secret_hash, claimed, created_at, last_seen_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (candidate_id, secret_hash, _iso(now), _iso(now)),
            )
            claimed = False
        else:
            if not hmac.compare_digest(str(row["secret_hash"]), secret_hash):
                raise RelayAuthError("Invalid HomeServer device credential.")
            claimed = bool(row["claimed"])
            connection.execute(
                "UPDATE relay_devices SET last_seen_at=? WHERE device_id=?",
                (_iso(now), candidate_id),
            )

        if not claimed:
            claim_code = _new_claim_code()
            expires = now + timedelta(seconds=settings.claim_ttl_seconds)
            connection.execute(
                """
                UPDATE relay_devices
                SET claim_code_hash=?, claim_expires_at=?, last_seen_at=?
                WHERE device_id=?
                """,
                (_claim_hash(claim_code), _iso(expires), _iso(now), candidate_id),
            )

    return {
        "device_id": candidate_id,
        "claimed": claimed,
        "claim_code": claim_code,
    }


def claim_device(claim_code: str) -> dict:
    normalized = _normalize_claim_code(claim_code)
    if len(normalized) != 12:
        raise RelayClaimError("Claim code was not found or has expired.")
    digest = _claim_hash(normalized)
    now = _now()
    token = _new_session_token()
    token_hash = _sha256(token)

    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT device_id, claim_expires_at
            FROM relay_devices
            WHERE claimed=0 AND claim_code_hash=?
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
        if row is None:
            raise RelayClaimError("Claim code was not found or has expired.")
        try:
            expires = datetime.fromisoformat(str(row["claim_expires_at"]))
        except (TypeError, ValueError):
            expires = now - timedelta(seconds=1)
        if expires <= now:
            connection.execute(
                "UPDATE relay_devices SET claim_code_hash=NULL, claim_expires_at=NULL WHERE device_id=?",
                (row["device_id"],),
            )
            raise RelayClaimError("Claim code was not found or has expired.")

        device_id = str(row["device_id"])
        updated = connection.execute(
            """
            UPDATE relay_devices
            SET claimed=1, claim_code_hash=NULL, claim_expires_at=NULL, last_seen_at=?
            WHERE device_id=? AND claimed=0 AND claim_code_hash=?
            """,
            (_iso(now), device_id, digest),
        )
        if updated.rowcount != 1:
            raise RelayClaimError("Claim code was not found or has expired.")
        connection.execute(
            """
            INSERT INTO relay_sessions(device_id, token_hash, created_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (device_id, token_hash, _iso(now), _iso(now)),
        )

    return {"device_id": device_id, "relay_token": token}


def authenticate_session(token: str) -> dict:
    candidate = str(token or "").strip()
    if len(candidate) < 40 or len(candidate) > 512:
        raise RelayAuthError("Invalid relay session.")
    digest = _sha256(candidate)
    now = _iso()
    with db() as connection:
        row = connection.execute(
            """
            SELECT s.id, s.device_id
            FROM relay_sessions s
            JOIN relay_devices d ON d.device_id=s.device_id
            WHERE s.token_hash=? AND s.revoked_at IS NULL AND d.claimed=1
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
        if row is None:
            raise RelayAuthError("Invalid relay session.")
        connection.execute(
            "UPDATE relay_sessions SET last_seen_at=? WHERE id=?",
            (now, row["id"]),
        )
        return {"session_id": int(row["id"]), "device_id": str(row["device_id"])}


def rotate_session(token: str) -> dict:
    candidate = str(token or "").strip()
    if len(candidate) < 40 or len(candidate) > 512:
        raise RelayAuthError("Invalid relay session.")
    digest = _sha256(candidate)
    now = _iso()
    new_token = _new_session_token()
    new_hash = _sha256(new_token)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT s.id, s.device_id
            FROM relay_sessions s
            JOIN relay_devices d ON d.device_id=s.device_id
            WHERE s.token_hash=? AND s.revoked_at IS NULL AND d.claimed=1
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
        if row is None:
            raise RelayAuthError("Invalid relay session.")
        updated = connection.execute(
            "UPDATE relay_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (now, row["id"]),
        )
        if updated.rowcount != 1:
            raise RelayAuthError("Invalid relay session.")
        device_id = str(row["device_id"])
        connection.execute(
            """
            INSERT INTO relay_sessions(device_id, token_hash, created_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (device_id, new_hash, now, now),
        )
    return {"device_id": device_id, "relay_token": new_token}


def record_event(
    event: str,
    status: str,
    *,
    device_id: str | None = None,
    operation: str | None = None,
    request_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    safe = metadata if isinstance(metadata, dict) else {}
    with db() as connection:
        connection.execute(
            """
            INSERT INTO relay_events(device_id, event, status, operation, request_id, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(device_id)[:80] if device_id else None,
                str(event)[:120],
                str(status)[:40],
                str(operation)[:80] if operation else None,
                str(request_id)[:128] if request_id else None,
                json.dumps(safe, separators=(",", ":")),
                _iso(),
            ),
        )


def database_path() -> Path:
    return settings.database_path

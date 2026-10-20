from __future__ import annotations

"""Owner-scoped outbound HTTPS links to self-hosted Food Monster instances.

No server-side access token goes into SQLite, app logs, the Remote Bridge or
agent prompt context. The Food Monster remains authoritative for all data.
"""
import ipaddress
import json
import os
import re
import socket
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..database import db
from .owner_secret import _atomic_write, _protect_windows, _unprotect_windows

router = APIRouter(prefix="/api/v1/control/food-monster", tags=["Food Monster"])
_LOCK = threading.RLock()
_ID = re.compile(r"^[a-f0-9]{32}$")
_UUID = re.compile(r"^[a-f0-9-]{36}$")
_CODE = re.compile(r"^fmlink_[A-Za-z0-9_-]{43}$")
_TOKEN = re.compile(r"^fm2_[A-Za-z0-9_-]{43}$")
_TYPES = {"menu", "recipe"}


class FoodMonsterLinkError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class ConnectRequest(BaseModel):
    origin: str = Field(min_length=12, max_length=255)
    pairing_code: str = Field(min_length=50, max_length=70)
    label: str = Field(min_length=1, max_length=80)


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=160)
    type: str = Field(default="all")
    limit: int = Field(default=10, ge=1, le=20)


def normalize_origin(value: str) -> str:
    value = value.strip()
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        raise FoodMonsterLinkError("Enter a valid public HTTPS Food Monster hostname.")
    try:
        port = parts.port
    except ValueError:
        raise FoodMonsterLinkError("Invalid HTTPS port.")
    if (parts.scheme != "https" or not host or port is not None
        or parts.username is not None or parts.password is not None
        or parts.path not in ("", "/") or parts.query or parts.fragment
        or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host)
        or "." not in host or host.startswith(".") or host.endswith(".")
        or ".." in host or host.endswith((".local", ".internal", ".localhost"))
        or any(not x or x.startswith("-") or x.endswith("-") for x in host.split("."))):
        raise FoodMonsterLinkError("Only a public HTTPS subdomain or domain without a path, port or credentials is allowed.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise FoodMonsterLinkError("IP-address origins are not allowed.")
    return "https://" + host


def require_public_dns(origin: str) -> None:
    host = urlsplit(normalize_origin(origin)).hostname
    try:
        resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise FoodMonsterLinkError("Food Monster hostname could not be securely resolved.")
    if not resolved or any(not ipaddress.ip_address(item[4][0]).is_global for item in resolved):
        raise FoodMonsterLinkError("The hostname must resolve only to public internet addresses.")


def _vault_path() -> Path:
    return settings.data_dir / "security" / "food-monster-links.dat"


def _vault_read() -> dict[str, str]:
    path = _vault_path()
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if os.name == "nt":
        raw = _unprotect_windows(raw)
    value = json.loads(raw.decode("utf-8"))
    if (not isinstance(value, dict)
        or any(not isinstance(k, str) or not _ID.fullmatch(k)
               or not isinstance(v, str) or not _TOKEN.fullmatch(v)
               for k, v in value.items())):
        raise FoodMonsterLinkError("Protected connection credentials are invalid.", 503)
    return value


def _vault_write(entries: dict[str, str]) -> None:
    raw = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    _atomic_write(_vault_path(), _protect_windows(raw) if os.name == "nt" else raw)


def _request(origin: str, payload: dict | None = None, token: str | None = None) -> dict:
    origin = normalize_origin(origin)
    require_public_dns(origin)
    url = origin + "/api/food-monster-vp3.php"
    headers = {"Accept": "application/json", "Cache-Control": "no-store"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with httpx.Client(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=False, trust_env=False) as client:
            response = client.get(url, headers=headers) if payload is None else client.post(
                url, headers=headers, json=payload)
        if 300 <= response.status_code < 400:
            raise FoodMonsterLinkError("Food Monster redirect blocked for security.", 502)
        if response.status_code in (401, 403):
            raise FoodMonsterLinkError("Food Monster rejected the connection credentials.", 403)
        if response.status_code >= 400:
            raise FoodMonsterLinkError("Food Monster connector request failed.", 502)
        if len(response.content) > 262144:
            raise FoodMonsterLinkError("Food Monster response exceeds size limit.", 502)
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise FoodMonsterLinkError("Food Monster returned an invalid response.", 502)
        return result
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, FoodMonsterLinkError):
            raise
        raise FoodMonsterLinkError("Unable to securely reach Food Monster.", 502) from None


def _sanitize_record(row: dict) -> dict:
    return {"id": row["id"], "origin": row["origin"], "instanceId": row["instance_id"],
        "label": row["label"], "scopes": json.loads(row["scopes_json"]),
        "status": row["status"], "addedAt": row["added_at"],
        "lastCheckedAt": row["last_checked_at"], "lastError": row["last_error"]}


def list_connections() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM food_monster_connections ORDER BY added_at DESC").fetchall()
    return [_sanitize_record(dict(x)) for x in rows]


def connect_instance(origin: str, code: str, label: str) -> dict:
    origin = normalize_origin(origin)
    if not _CODE.fullmatch(code):
        raise FoodMonsterLinkError("Enter a valid one-time Food Monster pairing code.")
    label = label.strip()
    if not label or len(label) > 80:
        raise FoodMonsterLinkError("Connection label must contain 1 to 80 characters.")
    with _LOCK:
        discovery = _request(origin)
        instance = str(discovery.get("instanceId") or "").lower()
        if discovery.get("schema") != "food-monster-vp3-v1" or not _UUID.fullmatch(instance):
            raise FoodMonsterLinkError("This site does not support the expected Food Monster protocol.")
        if "knowledge.search" not in (discovery.get("capabilities") or []):
            raise FoodMonsterLinkError("This Food Monster does not offer approved knowledge search.")
        with db() as conn:
            exists = conn.execute("SELECT id FROM food_monster_connections WHERE origin=? AND instance_id=?",
                                  (origin, instance)).fetchone()
        if exists:
            raise FoodMonsterLinkError("This Food Monster is already connected.", 409)
        # Both calls go to the exact pinned origin; never follow an HTTP redirect.
        exchanged = _request(origin, {"action": "exchange", "code": code})
        token = exchanged.get("accessToken")
        remote_id = exchanged.get("connectionId")
        scopes = exchanged.get("scopes")
        if (exchanged.get("schema") != "food-monster-vp3-v1"
            or str(exchanged.get("instanceId") or "").lower() != instance
            or not isinstance(token, str) or not _TOKEN.fullmatch(token)
            or not isinstance(remote_id, str) or not _ID.fullmatch(remote_id)
            or not isinstance(scopes, list) or not scopes
            or len(set(scopes)) != len(scopes) or any(s not in _TYPES for s in scopes)):
            raise FoodMonsterLinkError("Pairing response failed identity and permission validation.", 502)
        local_id = uuid.uuid4().hex
        saved = False
        try:
            tokens = _vault_read()
            tokens[local_id] = token
            _vault_write(tokens)
            saved = True
            with db() as conn:
                conn.execute("""INSERT INTO food_monster_connections
                     (id,origin,instance_id,remote_connection_id,label,scopes_json,status)
                     VALUES (?,?,?,?,?,?,'active')""",
                     (local_id,origin,instance,remote_id,label,json.dumps(scopes)))
        except Exception:
            if saved:
                tokens = _vault_read()
                tokens.pop(local_id, None)
                _vault_write(tokens)
            raise
        return {"connection": _sanitize_record({"id": local_id, "origin": origin,
            "instance_id": instance, "label": label, "scopes_json": json.dumps(scopes),
            "status": "active", "added_at": datetime.now(timezone.utc).isoformat(),
            "last_checked_at": None, "last_error": ""})}


def search_instance(local_id: str, query: str, kind: str = "all", limit: int = 10) -> dict:
    if not _ID.fullmatch(local_id) or kind not in _TYPES | {"all"} or not 1 <= limit <= 20:
        raise FoodMonsterLinkError("Invalid connection, category or limit.")
    if not query.strip() or len(query) > 160:
        raise FoodMonsterLinkError("Enter a search query of 1–160 characters.")
    with _LOCK:
        with db() as conn:
            row = conn.execute("SELECT * FROM food_monster_connections WHERE id=? AND status='active'",
                               (local_id,)).fetchone()
        if not row:
            raise FoodMonsterLinkError("Connection not found.", 404)
        record = dict(row)
        scopes = json.loads(record["scopes_json"])
        if kind != "all" and kind not in scopes:
            raise FoodMonsterLinkError("That data category was not approved.", 403)
        token = _vault_read().get(local_id)
        if not token:
            raise FoodMonsterLinkError("Connection credentials unavailable.", 403)
        result = _request(record["origin"], {"action": "search", "q": query.strip(),
                   "type": kind, "limit": limit}, token=token)
        if result.get("schema") != "food-monster-knowledge-v1" or not isinstance(result.get("results"), list):
            raise FoodMonsterLinkError("Food Monster knowledge response is not recognized.", 502)
        if len(result["results"]) > limit:
            raise FoodMonsterLinkError("Food Monster returned too many results.", 502)
        # Every result retains the exact source installation for grounded citations.
        for item in result["results"]:
            if not isinstance(item, dict) or item.get("type") not in scopes:
                raise FoodMonsterLinkError("Food Monster returned data outside the approved scope.", 502)
        with db() as conn:
            conn.execute("UPDATE food_monster_connections SET last_checked_at=CURRENT_TIMESTAMP,last_error='' WHERE id=?",
                         (local_id,))
        return {"origin": record["origin"], "instanceId": record["instance_id"],
                "connectionId": local_id, "readOnly": True, "results": result["results"],
                "count": len(result["results"])}


def disconnect_instance(local_id: str) -> dict:
    """Invalidate the exact Gelato bearer before deleting our local token.

    If Gelato cannot confirm the revocation, still erase the local secret
    and return an explicit manual source-side revocation requirement.
    Never claim remote success on a timeout or identity mismatch.
    """
    if not _ID.fullmatch(local_id):
        raise FoodMonsterLinkError("Invalid connection identifier.")
    with _LOCK:
        with db() as conn:
            row = conn.execute(
                "SELECT id,origin,instance_id,remote_connection_id "
                "FROM food_monster_connections WHERE id=?", (local_id,)
            ).fetchone()
        if not row:
            raise FoodMonsterLinkError("Connection not found.", 404)
        connection = dict(row)
        tokens = _vault_read()
        token = tokens.get(local_id)
        remote_revoked = False
        if token:
            try:
                result = _request(
                    connection["origin"], {"action": "revoke"}, token=token
                )
                remote_revoked = (
                    result.get("schema") == "food-monster-vp3-v1"
                    and result.get("revoked") is True
                    and str(result.get("instanceId") or "").lower() == connection["instance_id"]
                    and result.get("connectionId") == connection["remote_connection_id"]
                )
            except FoodMonsterLinkError:
                remote_revoked = False

        # Local access is always removed. A failed remote call cannot keep a
        # disconnected credential available in the owner's secret store.
        tokens.pop(local_id, None)
        _vault_write(tokens)
        with db() as conn:
            conn.execute("DELETE FROM food_monster_connections WHERE id=?", (local_id,))
        return {
            "disconnected": True,
            "remoteRevoked": bool(remote_revoked),
            "remoteRevocationRequired": not remote_revoked,
            "message": (
                "Connection revoked on Food Monster and removed from HomeServer."
                if remote_revoked else
                "Local connection removed. Remote revocation could not be verified; "
                "open the Food Monster owner approval page at the same installation "
                "and revoke this connection there."
            ),
        }


@router.get("/connections")
def routes_list() -> dict:
    return {"items": list_connections()}


@router.post("/connections")
def routes_connect(payload: ConnectRequest) -> dict:
    try:
        return connect_instance(payload.origin, payload.pairing_code, payload.label)
    except FoodMonsterLinkError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.post("/connections/{local_id}/search")
def routes_search(local_id: str, payload: SearchRequest) -> dict:
    try:
        return search_instance(local_id, payload.q, payload.type, payload.limit)
    except FoodMonsterLinkError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.delete("/connections/{local_id}")
def routes_disconnect(local_id: str) -> dict:
    try:
        return disconnect_instance(local_id)
    except FoodMonsterLinkError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None

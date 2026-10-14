"""VP3 native Stonefellow pairing: admin and fan identities never overlap.

Each HomeServer member explicitly authorizes their *own* logged-in Stonefellow
account. The Cloud is the authority for digital rights, print licenses and
administrative scopes. No credential is returned to the browser or Agent Chat.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import socket
from urllib.parse import urlsplit

import httpx

from . import homeserver_app_security, homeserver_apps, members

APP_KEY = "vp3.stonefellow"
CONTRACT = "vp3.stonefellow.native-pair.v1"
PAIR_PATH = "/api/homeserver-pair.php"
FAN_PATH = "/api/homeserver-library.php"
ADMIN_PATH = "/api/homeserver-admin.php"
SCOPES = {"fan.library.read", "admin.presence.read"}


class StonefellowError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def normalize_origin(value: str, *, resolver=None) -> str:
    parts = urlsplit(str(value).strip())
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or not host or parts.username or parts.password
        or parts.query or parts.fragment or parts.path not in ("", "/")
        or host == "localhost" or host.endswith((".local", ".internal", ".localhost"))
        or parts.port not in (None, 443)):
        raise StonefellowError("Stonefellow must use an approved public HTTPS origin.", 422)
    try:
        ipaddress.ip_address(host)
        raise StonefellowError("Stonefellow must use a DNS hostname, not an IP.", 422)
    except ValueError:
        pass
    if "." not in host or not re.fullmatch(r"[a-z0-9.-]{4,253}", host):
        raise StonefellowError("Invalid Stonefellow hostname.", 422)
    resolve = resolver or (lambda h: [v[4][0] for v in socket.getaddrinfo(h, 443, type=socket.SOCK_STREAM)])
    try:
        ips = resolve(host)
    except (OSError, ValueError) as exc:
        raise StonefellowError("Could not validate Stonefellow hostname.", 422) from exc
    if not ips:
        raise StonefellowError("Stonefellow hostname has no public address.", 422)
    try:
        if any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise StonefellowError("Stonefellow hostname resolves to a private address.", 422)
    except ValueError as exc:
        raise StonefellowError("Stonefellow DNS address is invalid.", 422) from exc
    return "https://" + host


def _app() -> None:
    try:
        existing = homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise StonefellowError("Install VP3 Stonefellow first.", 404) from exc
    if not existing.get("installed_version"):
        raise StonefellowError("Install VP3 Stonefellow first.", 404)


def _vault_key(member_id: str, prefix: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", member_id):
        raise StonefellowError("Invalid HomeServer member ID.", 422)
    return prefix + "_" + hashlib.sha256(member_id.encode()).hexdigest().upper()[:32]


def _allowed_member(member_id: str) -> None:
    _app()
    member = members.get_member(member_id)
    if member.get("status") != "active" or member.get("role") == "guest":
        raise StonefellowError("An active HomeServer member account is required.", 403)
    apps = members.assigned_apps(member_id).get("items", [])
    if not any(a.get("app_key") == APP_KEY and a.get("allowed") for a in apps):
        raise StonefellowError("HomeServer owner must enable Stonefellow for this member.", 403)


def _post(origin: str, body: dict, *, client=None, resolver=None) -> dict:
    host = normalize_origin(origin, resolver=resolver)
    try:
        if client is not None:
            response = client.post(host + PAIR_PATH, json=body)
        else:
            with httpx.Client(timeout=httpx.Timeout(20.0, connect=6.0),
                              follow_redirects=False, trust_env=False, verify=True) as connection:
                response = connection.post(host + PAIR_PATH, json=body)
        if response.status_code != 200:
            raise StonefellowError("Stonefellow pairing was denied or expired.", 403)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise StonefellowError("Stonefellow pairing service could not be reached.", 503) from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise StonefellowError("Stonefellow pairing returned an invalid response.", 502)
    return data


def _get(origin: str, token: str, path: str, *, client=None, resolver=None) -> dict:
    host = normalize_origin(origin, resolver=resolver)
    if path not in (FAN_PATH, ADMIN_PATH) or not re.fullmatch(r"sfhs_[a-f0-9]{64}", token):
        raise StonefellowError("Invalid Stonefellow account capability.", 403)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        if client is not None:
            result = client.get(host + path, headers=headers)
        else:
            with httpx.Client(timeout=httpx.Timeout(15.0, connect=6.0),
                              follow_redirects=False, trust_env=False, verify=True) as connection:
                result = connection.get(host + path, headers=headers)
        if result.status_code != 200:
            raise StonefellowError("Stonefellow authorization was revoked.", 401)
        data = result.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise StonefellowError("Stonefellow connection is unavailable.", 503) from exc
    expected = "stonefellow.fan-music.v1" if path == FAN_PATH else "stonefellow.admin-presence.v1"
    if not isinstance(data, dict) or data.get("ok") is not True or data.get("contract") != expected:
        raise StonefellowError("Stonefellow connection returned the wrong capability.", 502)
    return data


def configure_origin(origin: str, *, resolver=None) -> dict:
    _app()
    host = normalize_origin(origin, resolver=resolver)
    # Owner-configured site origin. Fan and admin credentials share no authority.
    homeserver_app_security.set_secret(APP_KEY, "CLOUD_ORIGIN", host)
    return {"contract": CONTRACT, "origin_configured": True, "admin_connected": False}


def start_pair(scope: str, *, member_id: str = "", label: str = "VP3 HomeServer",
               client=None, resolver=None) -> dict:
    _app()
    if scope not in SCOPES:
        raise StonefellowError("Unknown Stonefellow pairing scope.", 422)
    if scope == "fan.library.read":
        _allowed_member(member_id)
        pending_key = _vault_key(member_id, "PENDING_FAN")
        linked_key = _vault_key(member_id, "FAN")
        binding = "M" + hashlib.sha256(member_id.encode()).hexdigest()[:32]
    else:
        if member_id:
            raise StonefellowError("Admin presence must be initiated by the HomeServer owner.", 403)
        pending_key, linked_key, binding = "PENDING_ADMIN", "ADMIN_CONNECTION_TOKEN", "HOME_OWNER"
    origin = homeserver_app_security.get_secret(APP_KEY, "CLOUD_ORIGIN")
    if not origin:
        raise StonefellowError("HomeServer owner must configure the Stonefellow site first.")
    verifier = secrets.token_hex(32)
    response = _post(origin, {"operation": "start", "scope": scope,
                              "verifier": verifier, "label": label,
                              "member_binding": binding}, client=client, resolver=resolver)
    request_id = response.get("request_id")
    if (not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{48}", request_id)
        or response.get("scope") != scope
        or response.get("contract") != "stonefellow.homeserver-pair.v1"
        or response.get("status") != "pending"):
        raise StonefellowError("Stonefellow returned an invalid consent request.", 502)
    # Persist pending verifier in the HomeServer protected app vault, NOT the UI.
    homeserver_app_security.set_secret(APP_KEY, pending_key, json.dumps(
        {"request_id": request_id, "verifier": verifier, "scope": scope,
         "member_binding": binding}, separators=(",", ":")))
    return {"contract": CONTRACT, "status": "authorization_required",
            "request_id": request_id,
            "authorization_url": origin + "/homeserver-connect.php?request_id=" + request_id,
            "expires_at": response.get("expires_at"),
            "scope": scope, "account_login_required": True,
            "explicit_stonefellow_consent_required": True,
            "existing_account_auto_connected": False,
            "member_scoped": scope == "fan.library.read"}


def finish_pair(scope: str, *, member_id: str = "", client=None, resolver=None) -> dict:
    _app()
    if scope == "fan.library.read":
        _allowed_member(member_id)
        pending_key, linked_key = _vault_key(member_id, "PENDING_FAN"), _vault_key(member_id, "FAN")
        binding = "M" + hashlib.sha256(member_id.encode()).hexdigest()[:32]
    elif scope == "admin.presence.read" and not member_id:
        pending_key, linked_key, binding = "PENDING_ADMIN", "ADMIN_CONNECTION_TOKEN", "HOME_OWNER"
    else:
        raise StonefellowError("Invalid HomeServer pairing scope.", 403)
    pending = homeserver_app_security.get_secret(APP_KEY, pending_key)
    if not pending:
        raise StonefellowError("Start a fresh Stonefellow authorization request first.", 404)
    try:
        p = json.loads(pending)
    except ValueError as exc:
        raise StonefellowError("Pending connection was damaged.", 409) from exc
    if p.get("scope") != scope or p.get("member_binding") != binding:
        raise StonefellowError("Pending pairing does not belong to this member.", 403)
    origin = homeserver_app_security.get_secret(APP_KEY, "CLOUD_ORIGIN")
    response = _post(origin, {"operation": "redeem",
                              "request_id": p["request_id"],
                              "verifier": p["verifier"]}, client=client, resolver=resolver)
    if response.get("status") == "pending":
        return {"contract": CONTRACT, "status": "awaiting_stonefellow_login_and_approval"}
    if response.get("status") == "denied":
        homeserver_app_security.remove_secret(APP_KEY, pending_key)
        return {"contract": CONTRACT, "status": "denied"}
    auth = response.get("credential_once")
    if (response.get("status") != "connected"
        or response.get("scope") != scope or response.get("member_binding") != binding
        or not isinstance(auth, dict) or auth.get("scope") != scope
        or not re.fullmatch(r"sfhs_[a-f0-9]{64}", str(auth.get("token", "")))):
        raise StonefellowError("Stonefellow pairing was not authorized for this member.", 403)
    # A failed vault write must surface as an uncertain redemption, never
    # auto-start a replacement pairing or silently grant a different member.
    homeserver_app_security.set_secret(APP_KEY, linked_key, auth["token"])
    homeserver_app_security.remove_secret(APP_KEY, pending_key)
    return {"contract": CONTRACT, "status": "connected", "scope": scope,
            "member_scoped": scope == "fan.library.read",
            "admin_privileges_shared": False if scope == "fan.library.read" else True}


def fan_library(member_id: str, *, client=None, resolver=None) -> dict:
    _allowed_member(member_id)
    origin = homeserver_app_security.get_secret(APP_KEY, "CLOUD_ORIGIN")
    token = homeserver_app_security.get_secret(APP_KEY, _vault_key(member_id, "FAN"))
    if not origin or not token:
        return {"contract": CONTRACT, "status": "not_connected", "tracks": [], "count": 0}
    data = _get(origin, token, FAN_PATH, client=client, resolver=resolver)
    items = data.get("tracks")
    if not isinstance(items, list) or len(items) > 10000:
        raise StonefellowError("Invalid Stonefellow library response.", 502)
    tracks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tracks.append({"id": str(item.get("id", ""))[:100],
                       "title": str(item.get("title", ""))[:180],
                       "artist": "Stonefellow",
                       "duration_seconds": int(item.get("duration_seconds", 0)),
                       "stream_available_on_stonefellow": bool(item.get("can_stream")),
                       "download_available_on_stonefellow": bool(item.get("can_download"))})
    return {"contract": CONTRACT, "status": "connected", "tracks": tracks,
            "count": len(tracks), "purchases_authoritative_on_stonefellow": True,
            "admin_privileges": False, "local_stream_gateway_active": False}


def fan_disconnect(member_id: str) -> dict:
    _allowed_member(member_id)
    homeserver_app_security.remove_secret(APP_KEY, _vault_key(member_id, "FAN"))
    homeserver_app_security.remove_secret(APP_KEY, _vault_key(member_id, "PENDING_FAN"))
    return {"contract": CONTRACT, "status": "disconnected", "stonefellow_revoke_recommended": True}


def admin_status(*, client=None, resolver=None) -> dict:
    _app()
    origin = homeserver_app_security.get_secret(APP_KEY, "CLOUD_ORIGIN")
    token = homeserver_app_security.get_secret(APP_KEY, "ADMIN_CONNECTION_TOKEN")
    if not origin or not token:
        return {"contract": CONTRACT, "status": "not_connected", "admin_connected": False}
    data = _get(origin, token, ADMIN_PATH, client=client, resolver=resolver)
    return {"contract": CONTRACT, "status": "connected", "admin_connected": True,
            "publishing_jobs": data.get("publishing_job_counts", {}),
            "catalog_track_count": int(data.get("catalog_track_count", 0)),
            "manufacturing_submission_enabled": False}


def admin_disconnect() -> dict:
    _app()
    homeserver_app_security.remove_secret(APP_KEY, "ADMIN_CONNECTION_TOKEN")
    homeserver_app_security.remove_secret(APP_KEY, "PENDING_ADMIN")
    return {"contract": CONTRACT, "status": "disconnected", "fan_links_affected": False}

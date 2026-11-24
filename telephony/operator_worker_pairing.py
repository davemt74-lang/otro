"""8A.4D: retrieve one approved paired-device worker credential in memory.

Uses the already protected HomeServer HTTPS session; no additional credential
file, background service, phone call, media transport or model request.
The configured Cloud origin is trusted local configuration, never a remote URL.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .operator_worker_https import (
    OperatorRegistrationHTTPS, RegistrationTransportError, cloud_registration_host,
    read_bounded_json_response,
)
from .operator_worker_protocol import WorkerCredential, OperatorWorkerClient, WorkerRegistrationError

CREDENTIAL_PATH = "/api/operator-worker-credential-8a4.php"
PAIRING_POLL_PATH = "/api/homeserver-https-poll-v1300.php"
_DEVICE = re.compile(r"hs-[0-9a-f]{24}")
_BEARER = re.compile(r"[A-Za-z0-9_-]{64}")
_HEX = re.compile(r"[0-9a-f]{64}")
_CAPS = ("session.recover", "session.simulate")
_FIELDS = {"credential_id", "owner_id", "device_id", "worker_id", "secret_hex",
           "capabilities", "execution_enabled", "live_certified"}


class PairedWorkerError(WorkerRegistrationError):
    """Paired worker credential is unavailable or the local pairing changed."""


@dataclass(frozen=True)
class PairingSnapshot:
    endpoint: str
    device_id: str
    session_token: str = field(repr=False)


def _validate_snapshot(origin: str, value: object) -> PairingSnapshot:
    cloud_registration_host(origin)
    if (not isinstance(value, PairingSnapshot) or value.endpoint != origin + PAIRING_POLL_PATH
            or not isinstance(value.device_id, str) or not _DEVICE.fullmatch(value.device_id)
            or not isinstance(value.session_token, str) or not _BEARER.fullmatch(value.session_token)):
        raise PairedWorkerError("A current pairing at the configured Cloud origin is required")
    return value


def _same(a: PairingSnapshot, b: PairingSnapshot) -> bool:
    return (a.endpoint == b.endpoint and a.device_id == b.device_id
            and hmac.compare_digest(a.session_token, b.session_token))


def _pairs(items: list[tuple]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise PairedWorkerError("Invalid credential response")
        result[key] = value
    return result


def _decode(raw: bytes, snapshot: PairingSnapshot) -> WorkerCredential:
    try:
        data = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs)
        if (not isinstance(data, dict) or set(data) != _FIELDS
                or data["worker_id"] != "operator-homeserver"
                or data["device_id"] != hashlib.sha256(
                    b"vp3.operator.device.v1\x00" + snapshot.device_id.encode("ascii")).hexdigest()[:32]
                or data["capabilities"] != list(_CAPS)
                or data["execution_enabled"] is not False or data["live_certified"] is not False
                or not isinstance(data["secret_hex"], str) or not _HEX.fullmatch(data["secret_hex"])):
            raise PairedWorkerError("Invalid credential response")
        return WorkerCredential(data["credential_id"], data["owner_id"], data["device_id"],
                                data["worker_id"], bytes.fromhex(data["secret_hex"]), _CAPS)
    except (ValueError, UnicodeError, RecursionError, WorkerRegistrationError) as exc:
        raise PairedWorkerError("Invalid paired worker credential response") from None


def _fetch(origin: str, snapshot: PairingSnapshot, factory: Callable) -> WorkerCredential:
    host = cloud_registration_host(origin)
    conn = None
    try:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        conn = factory(host, port=443, timeout=5.0, context=context)
        conn.request("POST", CREDENTIAL_PATH, body=b"{}", headers={
            "Authorization": "Bearer " + snapshot.session_token,
            "X-HomeServer-Device": snapshot.device_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-store",
            "Connection": "close",
        })
        response = conn.getresponse()
        if response.status != 200:
            raise PairedWorkerError("Cloud has not approved this paired worker")
        raw = read_bounded_json_response(response, response.getheaders())
        return _decode(raw, snapshot)
    except (OSError, http.client.HTTPException, ValueError, RegistrationTransportError) as exc:
        raise PairedWorkerError("Paired worker credential exchange unavailable") from None
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


class PairedOperatorWorker:
    """Explicit, local-process registration handle. No new secret on disk.

    A pairing change fences this handle permanently. The same verified pairing
    is checked before and after every exchange; the Cloud independently rechecks
    its own pairing transaction. A local disconnect is not a distributed lock.
    Never treat a reporting state as a grant to execute any real call.
    """
    def __init__(self, origin: str, reader: Callable[[], PairingSnapshot | None], *,
                 clock: Callable[[], int] | None = None, connection_factory: Callable | None = None):
        self._origin = origin
        self._reader = reader
        self._clock = clock or (lambda: int(time.time()))
        self._lock = threading.RLock()
        self._snapshot = _validate_snapshot(origin, reader())
        factory = connection_factory or http.client.HTTPSConnection
        credential = _fetch(origin, self._snapshot, factory)
        if not _same(self._snapshot, _validate_snapshot(origin, reader())):
            raise PairedWorkerError("Local pairing changed during credential retrieval")
        self._client: OperatorWorkerClient | None = OperatorWorkerClient(credential)
        self._transport: OperatorRegistrationHTTPS | None = OperatorRegistrationHTTPS(
            origin, self._client, clock=self._clock, connection_factory=factory)

    def _invalidate(self) -> None:
        self._transport = None
        self._client = None
        self._snapshot = None

    def _assert_current(self) -> None:
        if self._snapshot is None or self._client is None or self._transport is None:
            raise PairedWorkerError("Paired worker handle is closed")
        try:
            current = _validate_snapshot(self._origin, self._reader())
            if _same(self._snapshot, current):
                return
        except Exception:
            pass
        self._invalidate()
        raise PairedWorkerError("Local pairing changed; reopen an approved worker handle")

    def send(self, action: str, capabilities: tuple[str, ...] = ()) -> dict:
        with self._lock:
            self._assert_current()
            assert self._transport is not None
            try:
                return self._transport.send(action, capabilities)
            finally:
                self._assert_current()

    def retry_pending(self) -> dict:
        with self._lock:
            self._assert_current()
            assert self._transport is not None
            try:
                return self._transport.retry_pending()
            finally:
                self._assert_current()

    def status(self) -> dict:
        with self._lock:
            self._assert_current()
            assert self._client is not None
            return self._client.status(self._clock())

    def close(self) -> None:
        """Drop the in-memory handle. Explicit retirement is a separate action."""
        with self._lock:
            self._invalidate()


def open_current_paired_worker(origin: str) -> PairedOperatorWorker:
    """Production-shaped local entry point; callers opt in explicitly.

    Uses the existing local session and read-only remote identity. No default
    origin discovery, new pairing, secret persistence or service activation.
    """
    from app.database import db
    from app.services.https_bridge_session import SESSION_LOCK, load_https_session
    from app.services.remote_identity import read_existing_remote_device_id

    def reader() -> PairingSnapshot | None:
        with SESSION_LOCK:
            session = load_https_session()
            device = read_existing_remote_device_id()
            if session is None or device is None:
                return None
            # A saved token alone is insufficient after local approval is revoked
            # or the bridge is disabled/replaced. Read only the canonical local
            # account-pairing rows, without triggering remote identity repair.
            with db() as connection:
                bridge = connection.execute(
                    "SELECT enabled,transport,https_endpoint FROM remote_bridge_settings WHERE id=1"
                ).fetchone()
                approved = connection.execute(
                    "SELECT status FROM paired_apps WHERE app_key='vp3' LIMIT 1"
                ).fetchone()
            if (bridge is None or not bridge["enabled"] or bridge["transport"] != "vp3_https"
                    or bridge["https_endpoint"] != session["endpoint"]
                    or approved is None or approved["status"] != "active"):
                return None
            return PairingSnapshot(session["endpoint"], device, session["session_token"])

    return PairedOperatorWorker(origin, reader)

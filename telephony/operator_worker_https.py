"""8A.4B: explicit HTTPS transport for simulation-only worker registration.

No background loop, provisioning, public Cloud route, media or carrier action.
The caller configures the trusted Cloud origin out of band and owns retries.
A failed/uncertain exchange leaves the exact protocol request pending.
"""
from __future__ import annotations

import http.client
import re
import ssl
import time
from collections.abc import Callable

from .operator_worker_protocol import (
    MAX_FRAME_BYTES, OperatorWorkerClient, SignedFrame, WorkerRegistrationError,
)

REGISTRATION_PATH = "/api/operator-worker-registration-8a4.php"
SIGNATURE_HEADER = "X-VP3-Operator-Signature"
_DNS = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_MAC = re.compile(r"[0-9a-f]{64}")
_TIMEOUT = 5.0


class RegistrationTransportError(WorkerRegistrationError):
    """Registration response is unavailable or not authoritative."""


class OperatorRegistrationHTTPS:
    """Send a signed registration frame to one configured HTTPS origin.

    The URL is never taken from remote instructions or a signed response.
    urllib redirects/proxies are not used. Each exchange creates one verified
    TLS connection, accepts only status 200 and one bounded signed JSON body,
    and closes the connection. Socket timeout is not a hard wall-clock DNS
    deadline; production worker supervision is a separate deployment gate.
    """
    def __init__(
        self, origin: str, client: OperatorWorkerClient, *,
        clock: Callable[[], int] | None = None,
        connection_factory: Callable | None = None,
    ):
        if (not isinstance(origin, str) or not origin.startswith("https://")
                or not _DNS.fullmatch(origin[8:])):
            raise RegistrationTransportError("Configure an exact HTTPS Cloud DNS origin")
        if not isinstance(client, OperatorWorkerClient):
            raise RegistrationTransportError("Authenticated worker client is required")
        self._host = origin[8:]
        self._client = client
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._factory = connection_factory or http.client.HTTPSConnection

    @staticmethod
    def _single(headers: list[tuple[str, str]], key: str, *, required: bool = False) -> str | None:
        values = [value for name, value in headers if name.lower() == key.lower()]
        if len(values) > 1 or (required and not values):
            raise RegistrationTransportError("Invalid registration response headers")
        return values[0] if values else None

    def _exchange(self, frame: SignedFrame) -> dict:
        if (not isinstance(frame, SignedFrame) or type(frame.body) is not bytes
                or not 2 <= len(frame.body) <= MAX_FRAME_BYTES
                or not isinstance(frame.signature, str) or not _MAC.fullmatch(frame.signature)):
            raise RegistrationTransportError("Invalid registration request frame")
        conn = None
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            conn = self._factory(self._host, port=443, timeout=_TIMEOUT, context=context)
            conn.request("POST", REGISTRATION_PATH, body=frame.body, headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-store",
                "Connection": "close",
                SIGNATURE_HEADER: frame.signature,
            })
            response = conn.getresponse()
            if response.status != 200:
                # Never follow redirects or retry a status/error automatically.
                raise RegistrationTransportError("Cloud did not acknowledge registration")
            headers = response.getheaders()
            signature = self._single(headers, SIGNATURE_HEADER, required=True)
            content_type = self._single(headers, "Content-Type", required=True)
            content_length = self._single(headers, "Content-Length")
            transfer = self._single(headers, "Transfer-Encoding")
            encoding = self._single(headers, "Content-Encoding")
            if (signature is None or not _MAC.fullmatch(signature)
                    or content_type not in ("application/json", "application/json; charset=utf-8")
                    or encoding not in (None, "identity")
                    or transfer not in (None, "chunked")
                    or (content_length is not None and transfer is not None)):
                raise RegistrationTransportError("Invalid registration response format")
            expected = None
            if content_length is not None:
                if not re.fullmatch(r"[0-9]{1,4}", content_length):
                    raise RegistrationTransportError("Invalid registration response length")
                expected = int(content_length)
                if not 2 <= expected <= MAX_FRAME_BYTES:
                    raise RegistrationTransportError("Registration response exceeds size limit")
            raw = response.read(MAX_FRAME_BYTES + 1)
            if (type(raw) is not bytes or not 2 <= len(raw) <= MAX_FRAME_BYTES
                    or (expected is not None and len(raw) != expected)):
                raise RegistrationTransportError("Registration response length is invalid")
            return self._client.accept(SignedFrame(raw, signature), self._clock())
        except RegistrationTransportError:
            raise
        except (OSError, http.client.HTTPException, ValueError, WorkerRegistrationError) as exc:
            # Do not include URLs, frame contents, tokens or provider details in logs.
            raise RegistrationTransportError("Registration exchange is unresolved") from None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass

    def send(self, action: str, capabilities: tuple[str, ...] = ()) -> dict:
        """One attempt; never automatically retry or create a second sequence."""
        return self._exchange(self._client.request(action, self._clock(), capabilities))

    def retry_pending(self) -> dict:
        """Explicitly replay only the original still-fresh pending request."""
        return self._exchange(self._client.retry(self._clock()))

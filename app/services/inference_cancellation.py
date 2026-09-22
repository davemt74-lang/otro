from __future__ import annotations

import threading
from typing import Any


class InferenceCancelled(RuntimeError):
    pass


class CancellationToken:
    """Thread-safe cooperative cancellation for one Agent inference turn.

    Provider clients register while a blocking HTTP request is in flight.
    Cancelling closes those clients so httpx wakes promptly instead of waiting
    for the provider timeout. Callers also check the token before tools and
    persistence so a late provider result cannot continue the cancelled turn.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._event = threading.Event()
        self._reason = ""
        self._clients: set[Any] = set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason or "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._reason = str(reason or "cancelled")[:120]
            self._event.set()
            clients = list(self._clients)
        for client in clients:
            try:
                client.close()
            except Exception:
                pass

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise InferenceCancelled(self.reason)

    def register_client(self, client: Any) -> None:
        self.raise_if_cancelled()
        with self._lock:
            if self._event.is_set():
                try:
                    client.close()
                except Exception:
                    pass
                raise InferenceCancelled(self.reason)
            self._clients.add(client)

    def unregister_client(self, client: Any) -> None:
        with self._lock:
            self._clients.discard(client)

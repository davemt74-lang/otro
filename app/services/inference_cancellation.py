from __future__ import annotations

import threading
from typing import Any, Callable


class InferenceCancelled(RuntimeError):
    pass


class CancellationToken:
    """Thread-safe cooperative cancellation for one Agent inference turn.

    Provider clients register while a blocking HTTP request is in flight.
    Cancelling closes those clients so httpx wakes promptly instead of waiting
    for the provider timeout. Callers also check the token before tools and
    persistence so a late provider result cannot continue the cancelled turn.
    Native HTTP turns may opt into a terminal commit; longer physical/voice
    jobs keep their default ability to cancel after chat persistence.
    """

    def __init__(self, *, commit_is_terminal: bool = False) -> None:
        self._lock = threading.RLock()
        self._event = threading.Event()
        self._reason = ""
        self._clients: set[Any] = set()
        self._committed = False
        self._commit_is_terminal = commit_is_terminal

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason or "cancelled"

    @property
    def committed(self) -> bool:
        with self._lock:
            return self._committed

    def commit(self, commit: Callable[[], None]) -> None:
        """Order the final durable write against cancellation of this turn.

        Callers prepare their transaction first, so provider/network work and
        SQLite write-lock waits never hold this lock. If cancellation wins,
        raising here lets the transaction roll back before turn cleanup.
        """
        with self._lock:
            self.raise_if_cancelled()
            commit()
            self._committed = True

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._lock:
            if self._event.is_set() or (self._committed and self._commit_is_terminal):
                return False
            self._reason = str(reason or "cancelled")[:120]
            self._event.set()
            clients = list(self._clients)
        for client in clients:
            try:
                client.close()
            except Exception:
                pass
        return True

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

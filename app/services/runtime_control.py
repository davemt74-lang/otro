from __future__ import annotations

import threading
from collections.abc import Callable


_LOCK = threading.RLock()
_HANDLER: Callable[[str], bool] | None = None


def register_runtime_handler(handler: Callable[[str], bool] | None) -> None:
    global _HANDLER
    with _LOCK:
        _HANDLER = handler


def runtime_control_available() -> bool:
    with _LOCK:
        return _HANDLER is not None


def request_runtime_command(command: str) -> bool:
    if command not in {"restart", "shutdown"}:
        return False
    with _LOCK:
        handler = _HANDLER
    if handler is None:
        return False
    return bool(handler(command))

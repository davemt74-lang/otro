"""Serialize control and release changes for one app in this HomeServer process."""
from functools import wraps
from inspect import signature
from threading import RLock

_guard = RLock()
_locks = {}


def app_lock(app_key):
    key = str(app_key).strip().lower()
    with _guard:
        return _locks.setdefault(key, RLock())


def serialized(function):
    parameters = signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = parameters.bind(*args, **kwargs)
        key = bound.arguments.get("app_key", bound.arguments.get("catalog_key"))
        with app_lock(key):
            return function(*args, **kwargs)

    return wrapped

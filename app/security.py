from __future__ import annotations

import hmac
import secrets

from .services.owner_secret import load_or_create_owner_secret


# The bootstrap secret survives restarts only in the local protected store.
# The browser session secret is intentionally process-local so restarting
# HomeServer invalidates every existing owner cookie.
OWNER_CONTROL_TOKEN = load_or_create_owner_secret()
OWNER_SESSION_TOKEN = secrets.token_urlsafe(48)


def owner_token_matches(candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate, OWNER_CONTROL_TOKEN)


def issue_owner_session(candidate: str | None) -> str | None:
    if not owner_token_matches(candidate):
        return None
    return OWNER_SESSION_TOKEN


def owner_session_matches(candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate, OWNER_SESSION_TOKEN)

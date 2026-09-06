from __future__ import annotations

import hmac
import secrets


OWNER_CONTROL_TOKEN = secrets.token_urlsafe(32)
OWNER_SESSION_TOKEN = secrets.token_urlsafe(32)


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

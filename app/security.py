from __future__ import annotations

import hmac
import secrets


OWNER_CONTROL_TOKEN = secrets.token_urlsafe(32)


def owner_token_matches(candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate, OWNER_CONTROL_TOKEN)

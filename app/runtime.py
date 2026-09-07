from __future__ import annotations

from http.cookies import SimpleCookie

from starlette.requests import Request
from starlette.responses import JSONResponse

from .bridge import app as core_app
from .security import issue_owner_session, owner_session_matches

OWNER_COOKIE = "homeserver_owner"
PROTECTED_PREFIX = "/api/v1/control/"
PROTECTED_EXACT = {"/api/v1/pairing/approve", "/system", "/remote", "/tasks"}


def _cookie_value(scope: dict, name: str) -> str | None:
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    raw_cookie = headers.get(b"cookie")
    if not raw_cookie:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie.decode("latin-1"))
    except Exception:
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel else None


def _is_protected(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIX) or path in PROTECTED_EXACT


class OwnerGateway:
    def __init__(self, inner_app):
        self.inner_app = inner_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.inner_app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path == "/__owner/session":
            request = Request(scope, receive=receive)
            if request.method != "POST":
                response = JSONResponse({"detail": "Method not allowed"}, status_code=405)
                await response(scope, receive, send)
                return

            session_token = issue_owner_session(request.headers.get("x-homeserver-owner"))
            if session_token is None:
                response = JSONResponse({"detail": "Owner authorization failed"}, status_code=401)
                response.headers["Cache-Control"] = "no-store"
                await response(scope, receive, send)
                return

            response = JSONResponse({"ok": True})
            response.set_cookie(
                OWNER_COOKIE,
                session_token,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            await response(scope, receive, send)
            return

        if _is_protected(path):
            session = _cookie_value(scope, OWNER_COOKIE)
            if not owner_session_matches(session):
                response = JSONResponse({"detail": "Owner control authorization required"}, status_code=401)
                response.headers["Cache-Control"] = "no-store"
                await response(scope, receive, send)
                return

        await self.inner_app(scope, receive, send)


app = OwnerGateway(core_app)
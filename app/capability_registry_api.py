from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, Header, HTTPException

from .main import app
from .services import meeting_transcription
from .services.capability_registry import build_registry
from .services.meeting_transcription_control import install as install_meeting_transcription_control
from .services.meeting_transcription_remote import install as install_meeting_transcription_remote
from .services.pairing import authenticate

router = APIRouter()

# Phase 18.6 installs the concrete meeting transcription operation. Phase 18.8
# layers status/stop control on top of that authenticated relay surface.
install_meeting_transcription_remote()
install_meeting_transcription_control()

# HomeServer uses FastAPI's custom lifespan API, so router on_event shutdown
# handlers are not authoritative. Wrap the existing lifespan once and preserve
# its startup/teardown behavior while guaranteeing meeting subscribers stop.
if not getattr(app.state, "meeting_transcription_lifespan_v1860", False):
    _base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _meeting_transcription_lifespan(application):
        async with _base_lifespan(application):
            try:
                yield
            finally:
                meeting_transcription.stop_all()

    app.router.lifespan_context = _meeting_transcription_lifespan
    app.state.meeting_transcription_lifespan_v1860 = True


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


@router.get("/api/v1/capability-registry")
def capability_registry(identity: dict = Depends(_current_app)) -> dict:
    """Return only the capabilities visible to the authenticated paired app."""
    registry = build_registry(identity)
    # The paired registry exposes capability readiness only. Per-job production
    # state is available through the app-owned meeting.transcription.status
    # operation so one paired wrapper cannot infer another wrapper's activity.
    registry["meeting_transcription"] = meeting_transcription.status()
    return registry

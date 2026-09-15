from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from .services import meeting_transcription
from .services.capability_registry import build_registry
from .services.meeting_transcription_remote import install as install_meeting_transcription_remote
from .services.pairing import authenticate

router = APIRouter()

# Phase 18.6 is a first-class authenticated HomeServer remote capability. Install
# the operation during application import so the relay and registry stay in sync.
install_meeting_transcription_remote()


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
    registry["meeting_transcription"] = meeting_transcription.status()
    return registry


@router.on_event("shutdown")
def shutdown_meeting_transcription() -> None:
    """Stop local media subscribers before the HomeServer process exits."""
    meeting_transcription.stop_all()

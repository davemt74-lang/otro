from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from .services.capability_registry import build_registry
from .services.pairing import authenticate

router = APIRouter()


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
    return build_registry(identity)

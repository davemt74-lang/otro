from __future__ import annotations

from fastapi import APIRouter

from .services import release_readiness

router = APIRouter()


@router.get("/api/v1/control/vp3-os/release-readiness")
def owner_release_readiness() -> dict:
    return release_readiness.report()

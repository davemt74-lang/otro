from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .services import local_files
from .services.pairing import authenticate


router = APIRouter()


def _paired_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _file_app(identity: dict = Depends(_paired_app)) -> dict:
    if "files.read" not in identity.get("permissions", []):
        raise HTTPException(status_code=403, detail="Permission required: files.read")
    return identity


def _error(exc: local_files.LocalFileError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/api/v1/files")
def paired_file_list(
    q: str = Query(default="", max_length=240),
    limit: int = Query(default=20, ge=1, le=50),
    identity: dict = Depends(_file_app),
) -> dict:
    try:
        result = local_files.list_files(identity, q, limit)
    except local_files.LocalFileError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}


@router.get("/api/v1/files/{file_ref}")
def paired_file_read(
    file_ref: str,
    offset: int = Query(default=0, ge=0, le=10_000_000),
    max_chars: int = Query(default=6000, ge=1, le=12000),
    identity: dict = Depends(_file_app),
) -> dict:
    try:
        result = local_files.read_file(
            identity,
            file_ref,
            offset=offset,
            max_chars=max_chars,
        )
    except local_files.LocalFileError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}

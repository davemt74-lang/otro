from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import knowledge_backups
from .services.pairing import authenticate

router = APIRouter()


class ExternalKnowledgeUpsert(BaseModel):
    source_key: str = Field(min_length=8, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="transcription", min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=250000)
    metadata: dict = Field(default_factory=dict)


class ExternalKnowledgeStatus(BaseModel):
    source_key: str = Field(min_length=8, max_length=160)


class AssetBegin(BaseModel):
    source_key: str = Field(min_length=8, max_length=160)
    asset_key: str = Field(min_length=8, max_length=160)
    original_name: str = Field(min_length=1, max_length=180)
    media_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(ge=1, le=knowledge_backups.MAX_ASSET_BYTES)
    sha256: str = Field(min_length=64, max_length=64)


class AssetChunk(BaseModel):
    upload_id: str = Field(min_length=24, max_length=96)
    offset: int = Field(ge=0)
    data_base64: str = Field(min_length=1, max_length=180000)


class AssetCommit(BaseModel):
    upload_id: str = Field(min_length=24, max_length=96)


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _require_write(identity: dict) -> None:
    if "knowledge.write" not in set(identity.get("permissions") or []):
        raise HTTPException(status_code=403, detail="Missing knowledge.write permission")


def _call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except knowledge_backups.KnowledgeBackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/knowledge/external")
def external_knowledge_upsert(payload: ExternalKnowledgeUpsert, identity: dict = Depends(_current_app)) -> dict:
    _require_write(identity)
    return _call(
        knowledge_backups.upsert_external_knowledge,
        identity,
        source_key=payload.source_key,
        title=payload.title,
        kind=payload.kind,
        content=payload.content,
        metadata=payload.metadata,
    )


@router.post("/api/v1/knowledge/external/status")
def external_knowledge_status(payload: ExternalKnowledgeStatus, identity: dict = Depends(_current_app)) -> dict:
    _require_write(identity)
    return _call(knowledge_backups.external_backup_status, identity, source_key=payload.source_key)


@router.post("/api/v1/knowledge/external/assets/begin")
def external_asset_begin(payload: AssetBegin, identity: dict = Depends(_current_app)) -> dict:
    _require_write(identity)
    return _call(
        knowledge_backups.begin_asset_upload,
        identity,
        source_key=payload.source_key,
        asset_key=payload.asset_key,
        original_name=payload.original_name,
        media_type=payload.media_type,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256,
    )


@router.post("/api/v1/knowledge/external/assets/chunk")
def external_asset_chunk(payload: AssetChunk, identity: dict = Depends(_current_app)) -> dict:
    _require_write(identity)
    return _call(
        knowledge_backups.append_asset_chunk,
        identity,
        upload_id=payload.upload_id,
        offset=payload.offset,
        data_base64=payload.data_base64,
    )


@router.post("/api/v1/knowledge/external/assets/commit")
def external_asset_commit(payload: AssetCommit, identity: dict = Depends(_current_app)) -> dict:
    _require_write(identity)
    return _call(knowledge_backups.commit_asset_upload, identity, upload_id=payload.upload_id)

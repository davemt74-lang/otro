from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .services import knowledge_sources


router = APIRouter()


class KnowledgeSourceCreate(BaseModel):
    path: str = Field(min_length=1, max_length=4000)
    label: str = Field(default="", max_length=200)
    recursive: bool = True
    scan_interval_seconds: int = Field(default=120, ge=30, le=3600)
    excludes: list[str] = Field(default_factory=list, max_length=40)


class KnowledgeSourceUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    recursive: bool | None = None
    scan_interval_seconds: int | None = Field(default=None, ge=30, le=3600)
    excludes: list[str] | None = Field(default=None, max_length=40)


def _source_error(exc: knowledge_sources.KnowledgeSourceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/api/v1/control/knowledge/sources")
def control_knowledge_sources() -> dict:
    return {
        "items": knowledge_sources.list_sources(),
        "supported_extensions": sorted(knowledge_sources.SUPPORTED_EXTENSIONS),
        "default_excludes": list(knowledge_sources.DEFAULT_EXCLUDES),
    }


@router.post("/api/v1/control/knowledge/sources")
def control_knowledge_source_create(payload: KnowledgeSourceCreate) -> dict:
    try:
        source = knowledge_sources.create_source(
            payload.path,
            label=payload.label,
            recursive=payload.recursive,
            scan_interval_seconds=payload.scan_interval_seconds,
            excludes=payload.excludes,
        )
        result = knowledge_sources.scan_source(int(source["id"]))
        return {"created": True, **result}
    except knowledge_sources.KnowledgeSourceError as exc:
        raise _source_error(exc) from exc


@router.patch("/api/v1/control/knowledge/sources/{source_id}")
def control_knowledge_source_update(source_id: int, payload: KnowledgeSourceUpdate) -> dict:
    try:
        source = knowledge_sources.update_source(
            source_id,
            label=payload.label,
            enabled=payload.enabled,
            recursive=payload.recursive,
            scan_interval_seconds=payload.scan_interval_seconds,
            excludes=payload.excludes,
        )
        return {"updated": True, "source": source}
    except knowledge_sources.KnowledgeSourceError as exc:
        raise _source_error(exc) from exc


@router.post("/api/v1/control/knowledge/sources/{source_id}/scan")
def control_knowledge_source_scan(source_id: int) -> dict:
    try:
        return knowledge_sources.scan_source(source_id)
    except knowledge_sources.KnowledgeSourceError as exc:
        raise _source_error(exc) from exc


@router.get("/api/v1/control/knowledge/sources/{source_id}/files")
def control_knowledge_source_files(
    source_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    try:
        return {"items": knowledge_sources.list_source_files(source_id, limit=limit)}
    except knowledge_sources.KnowledgeSourceError as exc:
        raise _source_error(exc) from exc


@router.delete("/api/v1/control/knowledge/sources/{source_id}")
def control_knowledge_source_delete(
    source_id: int,
    keep_indexed: bool = Query(default=False),
) -> dict:
    try:
        return knowledge_sources.delete_source(source_id, keep_indexed=keep_indexed)
    except knowledge_sources.KnowledgeSourceError as exc:
        raise _source_error(exc) from exc

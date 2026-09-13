from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import knowledge_folder_mapping
from .services.pairing import authenticate
from .services.windows_integration import native_folder_picker_supported

router = APIRouter()


class FolderMappingCreate(BaseModel):
    collection_key: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=200)
    recursive: bool = True
    scan_interval_seconds: int = Field(default=120, ge=30, le=3600)
    excludes: list[str] = Field(default_factory=list, max_length=40)


class KnowledgeItemCreate(BaseModel):
    collection_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=250000)
    kind: str = Field(default="summary", min_length=1, max_length=40)


def _paired_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _require(permission: str):
    def dependency(identity: dict = Depends(_paired_app)) -> dict:
        if permission not in identity.get("permissions", []):
            raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
        return identity

    return dependency


def _error(exc: knowledge_folder_mapping.KnowledgeFolderMappingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/api/v1/knowledge/capabilities-v062")
def knowledge_mapping_capabilities() -> dict:
    """Public, non-sensitive capability advertisement for paired clients."""
    return {
        "version": "v0.62",
        "requires": {"knowledge_search": "v0.37"},
        "permissions": ["knowledge.search", "knowledge.write"],
        "operations": [
            "knowledge.collections.list",
            "knowledge.folders.list",
            "knowledge.folder.map",
            "knowledge.folder.unmap",
            "knowledge.item.write",
        ],
        "native_folder_picker": {
            "supported": native_folder_picker_supported(),
            "runs_on_homeserver": True,
            "caller_supplies_path": False,
            "absolute_path_exposed": False,
        },
        "collection_scoped": True,
        "knowledge_kind_scoped_writes": True,
        "citation_safe_search": True,
    }


@router.get("/api/v1/knowledge/collections-v062")
def paired_knowledge_collections(identity: dict = Depends(_require("knowledge.search"))) -> dict:
    try:
        result = knowledge_folder_mapping.list_collections_for_app(identity)
    except knowledge_folder_mapping.KnowledgeFolderMappingError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}


@router.get("/api/v1/knowledge/folder-mappings-v062")
def paired_knowledge_folder_mappings(identity: dict = Depends(_require("knowledge.search"))) -> dict:
    try:
        result = knowledge_folder_mapping.list_folder_mappings(identity)
    except knowledge_folder_mapping.KnowledgeFolderMappingError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}


@router.post("/api/v1/knowledge/folder-mappings-v062")
def paired_knowledge_folder_map(
    payload: FolderMappingCreate,
    identity: dict = Depends(_require("knowledge.write")),
) -> dict:
    try:
        result = knowledge_folder_mapping.create_folder_mapping(
            identity,
            payload.collection_key,
            label=payload.label,
            recursive=payload.recursive,
            scan_interval_seconds=payload.scan_interval_seconds,
            excludes=payload.excludes,
        )
    except knowledge_folder_mapping.KnowledgeFolderMappingError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}


@router.delete("/api/v1/knowledge/folder-mappings-v062/{mapping_id}")
def paired_knowledge_folder_unmap(
    mapping_id: str,
    identity: dict = Depends(_require("knowledge.write")),
) -> dict:
    try:
        result = knowledge_folder_mapping.delete_folder_mapping(identity, mapping_id)
    except knowledge_folder_mapping.KnowledgeFolderMappingError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}


@router.post("/api/v1/knowledge/items-v062")
def paired_knowledge_item_write(
    payload: KnowledgeItemCreate,
    identity: dict = Depends(_require("knowledge.write")),
) -> dict:
    try:
        result = knowledge_folder_mapping.create_collection_item(
            identity,
            payload.collection_key,
            title=payload.title,
            content=payload.content,
            kind=payload.kind,
        )
    except knowledge_folder_mapping.KnowledgeFolderMappingError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import app_scopes, knowledge_collections
from .services.pairing import authenticate

router = APIRouter()


class KnowledgeCollectionCreate(BaseModel):
    collection_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class KnowledgeCollectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class CollectionAssignment(BaseModel):
    collection_key: str = Field(min_length=1, max_length=64)


class AppCollectionScopeUpdate(BaseModel):
    collection_keys: list[str] = Field(default_factory=list, max_length=64)


def _error(exc: knowledge_collections.KnowledgeCollectionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _paired_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _knowledge_app(identity: dict = Depends(_paired_app)) -> dict:
    if "knowledge.search" not in identity.get("permissions", []):
        raise HTTPException(status_code=403, detail="Permission required: knowledge.search")
    return identity


def scoped_knowledge_search(identity: dict, query: str, limit: int) -> dict:
    """Intersect v0.37 collection scope with the existing knowledge-kind scope."""
    requested = max(1, min(int(limit), 50))
    result = knowledge_collections.search_for_app(identity, query, limit=50)
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    items = [
        item for item in result.get("items", [])
        if app_scopes.knowledge_kind_allowed(scope, item.get("kind"))
    ][:requested]
    result["items"] = items
    result["count"] = len(items)
    return result


@router.get("/api/v1/control/knowledge/collections")
def control_collections() -> dict:
    return {
        "items": knowledge_collections.list_collections(),
        "default_collection": knowledge_collections.DEFAULT_COLLECTION_KEY,
    }


@router.post("/api/v1/control/knowledge/collections")
def control_collection_create(payload: KnowledgeCollectionCreate) -> dict:
    try:
        return {
            "created": True,
            "collection": knowledge_collections.create_collection(
                payload.collection_key,
                payload.name,
                payload.description,
            ),
        }
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.patch("/api/v1/control/knowledge/collections/{collection_key}")
def control_collection_update(collection_key: str, payload: KnowledgeCollectionUpdate) -> dict:
    try:
        return {
            "updated": True,
            "collection": knowledge_collections.update_collection(
                collection_key,
                name=payload.name,
                description=payload.description,
            ),
        }
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.delete("/api/v1/control/knowledge/collections/{collection_key}")
def control_collection_delete(collection_key: str) -> dict:
    try:
        return knowledge_collections.delete_collection(collection_key)
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.put("/api/v1/control/knowledge/sources/{source_id}/collection")
def control_source_collection(source_id: int, payload: CollectionAssignment) -> dict:
    try:
        return {
            "updated": True,
            **knowledge_collections.assign_source(source_id, payload.collection_key),
        }
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.put("/api/v1/control/knowledge/{item_id}/collection")
def control_item_collection(item_id: int, payload: CollectionAssignment) -> dict:
    try:
        return {
            "updated": True,
            **knowledge_collections.assign_item(item_id, payload.collection_key),
        }
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.get("/api/v1/control/apps/{app_id}/knowledge-collections")
def control_app_collection_scope(app_id: int) -> dict:
    try:
        return knowledge_collections.app_collection_scope(app_id)
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.put("/api/v1/control/apps/{app_id}/knowledge-collections")
def control_app_collection_scope_update(app_id: int, payload: AppCollectionScopeUpdate) -> dict:
    try:
        return {
            "updated": True,
            "scope": knowledge_collections.set_app_collection_scope(app_id, payload.collection_keys),
        }
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc


@router.get("/api/v1/knowledge/search-v037")
def paired_collection_search(
    q: str = Query(default="", max_length=240),
    limit: int = Query(default=20, ge=1, le=50),
    identity: dict = Depends(_knowledge_app),
) -> dict:
    try:
        result = scoped_knowledge_search(identity, q, limit)
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise _error(exc) from exc
    return {**result, "app": identity["app_key"]}

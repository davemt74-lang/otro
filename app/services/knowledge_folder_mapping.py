from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from ..database import db
from . import app_scopes, knowledge, knowledge_collections, knowledge_sources
from .windows_integration import WindowsIntegrationError, pick_local_folder

_MAPPING_ID = re.compile(r"^source-(\d{1,18})$")
_SAFE_KIND = re.compile(r"^[a-z0-9_.:-]{1,40}$")


class KnowledgeFolderMappingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _identity_id(identity: dict[str, Any]) -> int:
    app_id = int(identity.get("id") or 0)
    if app_id < 1:
        raise KnowledgeFolderMappingError("Connected app identity is unavailable.", status_code=401)
    return app_id


def _allowed_collection_keys(identity: dict[str, Any]) -> set[str] | None:
    try:
        scope = knowledge_collections.app_collection_scope(_identity_id(identity))
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise KnowledgeFolderMappingError(str(exc), status_code=exc.status_code) from exc
    if not scope["restricted"]:
        return None
    return {str(item) for item in scope["collections"]}


def _collection_for_app(identity: dict[str, Any], collection_key: str) -> dict[str, Any]:
    try:
        key = knowledge_collections.normalize_collection_key(collection_key)
    except knowledge_collections.KnowledgeCollectionError as exc:
        raise KnowledgeFolderMappingError(str(exc), status_code=exc.status_code) from exc

    allowed = _allowed_collection_keys(identity)
    if allowed is not None and key not in allowed:
        raise KnowledgeFolderMappingError(
            "Knowledge collection is outside this application's allowed scope.",
            status_code=403,
        )

    collection = next(
        (item for item in knowledge_collections.list_collections() if str(item["collection_key"]) == key),
        None,
    )
    if collection is None:
        raise KnowledgeFolderMappingError("Knowledge collection not found.", status_code=404)
    return collection


def list_collections_for_app(identity: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_collection_keys(identity)
    items: list[dict[str, Any]] = []
    for collection in knowledge_collections.list_collections():
        key = str(collection["collection_key"])
        if allowed is not None and key not in allowed:
            continue
        items.append(
            {
                "collection_key": key,
                "name": str(collection["name"]),
                "description": str(collection.get("description") or ""),
                "source_count": int(collection.get("source_count") or 0),
                "direct_item_count": int(collection.get("direct_item_count") or 0),
            }
        )
    return {
        "items": items,
        "default_collection": knowledge_collections.DEFAULT_COLLECTION_KEY,
        "scope": {
            "restricted": allowed is not None,
            "collections": sorted(allowed) if allowed is not None else [],
        },
    }


def _mapping_rows() -> list[dict[str, Any]]:
    knowledge_collections.ensure_default_collection()
    with db() as connection:
        rows = connection.execute(
            """
            SELECT ks.id, ks.label, ks.enabled, ks.recursive, ks.scan_interval_seconds,
                   ks.status, ks.last_scan_completed_at,
                   COALESCE(c.collection_key, 'general') AS collection_key,
                   COALESCE(c.name, 'General') AS collection_name,
                   (SELECT COUNT(*) FROM knowledge_source_files sf WHERE sf.source_id=ks.id) AS tracked_files,
                   (SELECT COUNT(*) FROM knowledge_source_files sf WHERE sf.source_id=ks.id AND sf.status='indexed') AS indexed_files,
                   (SELECT COUNT(*) FROM knowledge_source_files sf WHERE sf.source_id=ks.id AND sf.status='error') AS error_files
            FROM knowledge_sources ks
            LEFT JOIN knowledge_collection_sources cs ON cs.source_id=ks.id
            LEFT JOIN knowledge_collections c ON c.id=cs.collection_id
            ORDER BY ks.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _safe_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "mapping_id": f"source-{int(row['id'])}",
        "label": str(row.get("label") or "Local folder")[:200],
        "collection_key": str(row.get("collection_key") or knowledge_collections.DEFAULT_COLLECTION_KEY),
        "collection_name": str(row.get("collection_name") or "General")[:120],
        "enabled": bool(row.get("enabled")),
        "recursive": bool(row.get("recursive")),
        "scan_interval_seconds": int(row.get("scan_interval_seconds") or 120),
        "status": str(row.get("status") or "pending")[:40],
        "last_scan_completed_at": row.get("last_scan_completed_at"),
        "tracked_files": int(row.get("tracked_files") or 0),
        "indexed_files": int(row.get("indexed_files") or 0),
        "error_files": int(row.get("error_files") or 0),
    }


def list_folder_mappings(identity: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_collection_keys(identity)
    items = []
    for row in _mapping_rows():
        key = str(row.get("collection_key") or knowledge_collections.DEFAULT_COLLECTION_KEY)
        if allowed is not None and key not in allowed:
            continue
        items.append(_safe_mapping(row))
    return {
        "items": items,
        "privacy": {
            "absolute_paths_exposed": False,
            "picker_runs_on_homeserver": True,
            "local_index": True,
            "full_documents_returned": False,
        },
    }


def _mapping_id(value: str) -> int:
    match = _MAPPING_ID.fullmatch(str(value or "").strip())
    if match is None:
        raise KnowledgeFolderMappingError("Invalid folder mapping identifier.")
    source_id = int(match.group(1))
    if source_id < 1:
        raise KnowledgeFolderMappingError("Invalid folder mapping identifier.")
    return source_id


def _row_for_mapping(identity: dict[str, Any], mapping_id: str) -> dict[str, Any]:
    source_id = _mapping_id(mapping_id)
    row = next((item for item in _mapping_rows() if int(item["id"]) == source_id), None)
    if row is None:
        raise KnowledgeFolderMappingError("Knowledge folder mapping not found.", status_code=404)
    _collection_for_app(identity, str(row.get("collection_key") or knowledge_collections.DEFAULT_COLLECTION_KEY))
    return row


def _existing_source_id(selected: Path) -> int | None:
    target = os.path.normcase(str(selected.resolve(strict=True)))
    for source in knowledge_sources.list_sources():
        try:
            current = os.path.normcase(str(Path(str(source["path"])).resolve(strict=False)))
        except (OSError, RuntimeError, KeyError, ValueError):
            continue
        if current == target:
            return int(source["id"])
    return None


def _log_app(identity: dict[str, Any], action: str, resource_key: str, metadata: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('app', ?, ?, 'knowledge', ?, ?)
            """,
            (
                str(identity.get("app_key") or "paired-app")[:160],
                action[:120],
                resource_key[:160],
                json.dumps(metadata, separators=(",", ":")),
            ),
        )


def create_folder_mapping(
    identity: dict[str, Any],
    collection_key: str,
    *,
    label: str = "",
    recursive: bool = True,
    scan_interval_seconds: int = 120,
    excludes: list[str] | None = None,
    picker: Callable[[str], Path | None] | None = None,
) -> dict[str, Any]:
    collection = _collection_for_app(identity, collection_key)
    choose = picker or pick_local_folder
    try:
        selected = choose(f"Select a folder for {collection['name']}")
    except WindowsIntegrationError as exc:
        raise KnowledgeFolderMappingError(str(exc), status_code=409) from exc
    if selected is None:
        return {"created": False, "cancelled": True}

    try:
        selected = Path(selected).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise KnowledgeFolderMappingError("The selected local folder could not be resolved.") from exc
    if not selected.is_dir():
        raise KnowledgeFolderMappingError("The selected knowledge source must be a folder.")

    source_id = _existing_source_id(selected)
    created = source_id is None
    try:
        if source_id is None:
            source = knowledge_sources.create_source(
                str(selected),
                label=str(label or selected.name or "Local folder")[:200],
                recursive=recursive,
                scan_interval_seconds=scan_interval_seconds,
                excludes=excludes or [],
            )
            source_id = int(source["id"])
        else:
            # An existing source may be reused only when the paired app could
            # already see its current collection. This prevents a scoped app
            # from moving an inaccessible source into its own collection.
            _row_for_mapping(identity, f"source-{source_id}")
            knowledge_sources.update_source(
                source_id,
                label=str(label).strip()[:200] if str(label or "").strip() else None,
                recursive=recursive,
                scan_interval_seconds=scan_interval_seconds,
                excludes=excludes or [],
            )

        knowledge_collections.assign_source(source_id, str(collection["collection_key"]))
        scan_state = "completed"
        try:
            knowledge_sources.scan_source(source_id)
        except knowledge_sources.KnowledgeSourceError as exc:
            if exc.status_code == 409:
                scan_state = "deferred"
            else:
                scan_state = "error"
    except KnowledgeFolderMappingError:
        raise
    except (knowledge_sources.KnowledgeSourceError, knowledge_collections.KnowledgeCollectionError) as exc:
        status = int(getattr(exc, "status_code", 422))
        raise KnowledgeFolderMappingError(str(exc), status_code=status) from exc

    row = next(item for item in _mapping_rows() if int(item["id"]) == source_id)
    mapping = _safe_mapping(row)
    _log_app(
        identity,
        "knowledge.folder_mapped" if created else "knowledge.folder_remapped",
        mapping["mapping_id"],
        {"collection_key": mapping["collection_key"], "scan_state": scan_state},
    )
    return {
        "created": created,
        "cancelled": False,
        "scan_state": scan_state,
        "mapping": mapping,
        "privacy": {"absolute_path_exposed": False},
    }


def delete_folder_mapping(identity: dict[str, Any], mapping_id: str) -> dict[str, Any]:
    row = _row_for_mapping(identity, mapping_id)
    safe = _safe_mapping(row)
    try:
        knowledge_sources.delete_source(int(row["id"]), keep_indexed=False)
    except knowledge_sources.KnowledgeSourceError as exc:
        raise KnowledgeFolderMappingError(str(exc), status_code=exc.status_code) from exc
    _log_app(
        identity,
        "knowledge.folder_unmapped",
        safe["mapping_id"],
        {"collection_key": safe["collection_key"]},
    )
    return {
        "deleted": True,
        "mapping_id": safe["mapping_id"],
        "collection_key": safe["collection_key"],
        "privacy": {"absolute_path_exposed": False},
    }


def create_collection_item(
    identity: dict[str, Any],
    collection_key: str,
    *,
    title: str,
    content: str,
    kind: str = "summary",
) -> dict[str, Any]:
    collection = _collection_for_app(identity, collection_key)
    normalized_kind = str(kind or "summary").strip().lower()
    if not _SAFE_KIND.fullmatch(normalized_kind):
        raise KnowledgeFolderMappingError("Knowledge kind is invalid.")
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    if not app_scopes.knowledge_kind_allowed(scope, normalized_kind):
        raise KnowledgeFolderMappingError(
            "Knowledge kind is outside this application's allowed scope.",
            status_code=403,
        )

    clean_title = str(title or "").strip()[:240]
    clean_content = str(content or "").strip()
    if not clean_title:
        raise KnowledgeFolderMappingError("Knowledge title is required.")
    if not clean_content:
        raise KnowledgeFolderMappingError("Knowledge content is required.")

    result = knowledge.create_knowledge_item(clean_title, normalized_kind, clean_content, None)
    item_id = int(result["id"])
    try:
        knowledge_collections.assign_item(item_id, str(collection["collection_key"]))
    except knowledge_collections.KnowledgeCollectionError as exc:
        knowledge.delete_knowledge_item(item_id)
        raise KnowledgeFolderMappingError(str(exc), status_code=exc.status_code) from exc

    _log_app(
        identity,
        "knowledge.item_created",
        str(item_id),
        {"collection_key": collection["collection_key"], "kind": normalized_kind},
    )
    return {
        "created": True,
        "item": {
            "id": item_id,
            "title": clean_title,
            "kind": normalized_kind,
            "collection_key": str(collection["collection_key"]),
            "collection_name": str(collection["name"]),
            "chunk_count": int(result.get("chunk_count") or 0),
        },
    }

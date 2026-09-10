from __future__ import annotations

from typing import Any

from ..database import db
from . import app_scopes, knowledge_collections


class KnowledgeCollectionPolicyError(RuntimeError):
    pass


def app_id_for_source(source_app_key: str) -> int | None:
    source = str(source_app_key or "").strip()
    if not source.startswith("app:"):
        return None
    app_key = source[4:].strip()
    if not app_key:
        return None
    with db() as connection:
        row = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key=? AND status='active' LIMIT 1",
            (app_key,),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def allowed_collection_keys(app_id: int) -> set[str] | None:
    scope = knowledge_collections.app_collection_scope(int(app_id))
    if not scope["restricted"]:
        return None
    return {str(value) for value in scope["collections"]}


def collection_keys_for_items(item_ids: list[int]) -> dict[int, str]:
    ids = sorted({int(value) for value in item_ids if int(value) > 0})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT ki.id,
                   COALESCE(wc.collection_key, dc.collection_key, 'general') AS collection_key
            FROM knowledge_items ki
            LEFT JOIN (
                SELECT ksf.knowledge_item_id, c.collection_key
                FROM knowledge_source_files ksf
                LEFT JOIN knowledge_collection_sources kcs ON kcs.source_id=ksf.source_id
                LEFT JOIN knowledge_collections c ON c.id=kcs.collection_id
                WHERE ksf.knowledge_item_id IS NOT NULL
            ) wc ON wc.knowledge_item_id=ki.id
            LEFT JOIN (
                SELECT kci.knowledge_item_id, c.collection_key
                FROM knowledge_collection_items kci
                JOIN knowledge_collections c ON c.id=kci.collection_id
            ) dc ON dc.knowledge_item_id=ki.id
            WHERE ki.id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    return {
        int(row["id"]): str(row["collection_key"] or knowledge_collections.DEFAULT_COLLECTION_KEY)
        for row in rows
    }


def filter_items_for_app(
    app_id: int,
    items: list[dict[str, Any]],
    *,
    apply_kind_scope: bool = True,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if int(app_id) < 1 or not items:
        return [] if int(app_id) < 1 else list(items)
    allowed = allowed_collection_keys(int(app_id))
    normalized_scope = app_scopes.normalize(scope if scope is not None else app_scopes.get_scope(int(app_id)))
    ids = [int(item.get("id") or 0) for item in items if int(item.get("id") or 0) > 0]
    collection_by_id = collection_keys_for_items(ids) if allowed is not None else {}
    output: list[dict[str, Any]] = []
    for item in items:
        item_id = int(item.get("id") or 0)
        if item_id < 1:
            continue
        if apply_kind_scope and not app_scopes.knowledge_kind_allowed(normalized_scope, item.get("kind")):
            continue
        if allowed is not None:
            collection_key = collection_by_id.get(item_id, knowledge_collections.DEFAULT_COLLECTION_KEY)
            if collection_key not in allowed:
                continue
        output.append(item)
    return output


def source_ref_allowed(source_app_key: str, ref: dict[str, Any]) -> bool:
    if str(ref.get("kind") or "") != "knowledge":
        return True
    try:
        item_id = int(ref.get("id") or 0)
    except (TypeError, ValueError):
        return False
    if item_id < 1:
        return False
    app_id = app_id_for_source(source_app_key)
    if app_id is None:
        return False
    with db() as connection:
        row = connection.execute(
            "SELECT id, kind FROM knowledge_items WHERE id=? LIMIT 1", (item_id,)
        ).fetchone()
    if row is None:
        return False
    return bool(filter_items_for_app(app_id, [dict(row)]))


def scoped_search(identity: dict[str, Any], query: str, limit: int = 20) -> dict[str, Any]:
    requested = max(1, min(int(limit), 50))
    # The collection search already applies collection scope and citation-safe
    # projection. Re-apply the canonical policy here so existing kind scopes
    # remain an intersection, never a replacement.
    result = knowledge_collections.search_for_app(identity, query, limit=50)
    app_id = int(identity.get("id") or 0)
    items = filter_items_for_app(
        app_id,
        [dict(item) for item in result.get("items", []) if isinstance(item, dict)],
        apply_kind_scope=True,
        scope=identity.get("scope"),
    )[:requested]
    result["items"] = items
    result["count"] = len(items)
    return result


def delete_collection_if_unused(collection_key: str) -> dict[str, Any]:
    collection = knowledge_collections._collection_row(collection_key)
    collection_id = int(collection["id"])
    if collection["collection_key"] == knowledge_collections.DEFAULT_COLLECTION_KEY:
        raise knowledge_collections.KnowledgeCollectionError(
            "The General collection cannot be deleted.", status_code=409
        )
    with db() as connection:
        usage = {
            "sources": int(connection.execute(
                "SELECT COUNT(*) FROM knowledge_collection_sources WHERE collection_id=?", (collection_id,)
            ).fetchone()[0]),
            "items": int(connection.execute(
                "SELECT COUNT(*) FROM knowledge_collection_items WHERE collection_id=?", (collection_id,)
            ).fetchone()[0]),
            "apps": int(connection.execute(
                "SELECT COUNT(*) FROM app_knowledge_collection_scopes WHERE collection_id=?", (collection_id,)
            ).fetchone()[0]),
        }
    if any(usage.values()):
        raise knowledge_collections.KnowledgeCollectionError(
            "Collection is still assigned. Move its sources/items and remove connected-app scopes before deleting it.",
            status_code=409,
        )
    return knowledge_collections.delete_collection(str(collection["collection_key"]))

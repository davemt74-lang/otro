from __future__ import annotations

import re
from typing import Any

from ..database import db

DEFAULT_COLLECTION_KEY = "general"
_COLLECTION_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


class KnowledgeCollectionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def normalize_collection_key(value: str) -> str:
    key = str(value or "").strip().lower()
    if not _COLLECTION_KEY.fullmatch(key):
        raise KnowledgeCollectionError(
            "Collection key must use 1-64 lowercase letters, numbers, dots, underscores, or hyphens."
        )
    return key


def ensure_default_collection() -> int:
    with db() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO knowledge_collections(collection_key, name, description)
            VALUES ('general', 'General', 'Default HomeServer knowledge collection.')
            """
        )
        row = connection.execute(
            "SELECT id FROM knowledge_collections WHERE collection_key=? LIMIT 1",
            (DEFAULT_COLLECTION_KEY,),
        ).fetchone()
    if row is None:
        raise KnowledgeCollectionError("Default knowledge collection is unavailable.", status_code=500)
    return int(row["id"])


def _collection_row(key: str) -> dict[str, Any]:
    normalized = normalize_collection_key(key)
    ensure_default_collection()
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, collection_key, name, description, created_at, updated_at
            FROM knowledge_collections WHERE collection_key=? LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    if row is None:
        raise KnowledgeCollectionError("Knowledge collection not found.", status_code=404)
    return dict(row)


def list_collections() -> list[dict[str, Any]]:
    ensure_default_collection()
    with db() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.collection_key, c.name, c.description, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM knowledge_collection_sources s WHERE s.collection_id=c.id) AS source_count,
                   (SELECT COUNT(*) FROM knowledge_collection_items i WHERE i.collection_id=c.id) AS direct_item_count,
                   (SELECT COUNT(*) FROM app_knowledge_collection_scopes a WHERE a.collection_id=c.id) AS app_scope_count
            FROM knowledge_collections c
            ORDER BY CASE WHEN c.collection_key='general' THEN 0 ELSE 1 END, c.name COLLATE NOCASE, c.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_collection(key: str, name: str, description: str = "") -> dict[str, Any]:
    normalized = normalize_collection_key(key)
    title = str(name or "").strip()[:120]
    if not title:
        raise KnowledgeCollectionError("Collection name is required.")
    with db() as connection:
        try:
            connection.execute(
                """
                INSERT INTO knowledge_collections(collection_key, name, description)
                VALUES (?, ?, ?)
                """,
                (normalized, title, str(description or "").strip()[:500]),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise KnowledgeCollectionError("That collection key already exists.", status_code=409) from exc
            raise
    return _collection_row(normalized)


def update_collection(key: str, *, name: str | None = None, description: str | None = None) -> dict[str, Any]:
    current = _collection_row(key)
    next_name = str(current["name"] if name is None else name).strip()[:120]
    if not next_name:
        raise KnowledgeCollectionError("Collection name is required.")
    next_description = str(current["description"] if description is None else description).strip()[:500]
    with db() as connection:
        connection.execute(
            """
            UPDATE knowledge_collections
            SET name=?, description=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (next_name, next_description, int(current["id"])),
        )
    return _collection_row(str(current["collection_key"]))


def delete_collection(key: str) -> dict[str, Any]:
    current = _collection_row(key)
    if current["collection_key"] == DEFAULT_COLLECTION_KEY:
        raise KnowledgeCollectionError("The General collection cannot be deleted.", status_code=409)
    with db() as connection:
        connection.execute("DELETE FROM knowledge_collections WHERE id=?", (int(current["id"]),))
    return {"deleted": True, "collection_key": current["collection_key"]}


def assign_source(source_id: int, collection_key: str) -> dict[str, Any]:
    collection = _collection_row(collection_key)
    with db() as connection:
        source = connection.execute(
            "SELECT id FROM knowledge_sources WHERE id=? LIMIT 1", (int(source_id),)
        ).fetchone()
        if source is None:
            raise KnowledgeCollectionError("Knowledge source not found.", status_code=404)
        connection.execute(
            """
            INSERT INTO knowledge_collection_sources(source_id, collection_id)
            VALUES (?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                collection_id=excluded.collection_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (int(source_id), int(collection["id"])),
        )
    return {"source_id": int(source_id), "collection": collection}


def assign_item(item_id: int, collection_key: str) -> dict[str, Any]:
    collection = _collection_row(collection_key)
    with db() as connection:
        item = connection.execute(
            "SELECT id FROM knowledge_items WHERE id=? LIMIT 1", (int(item_id),)
        ).fetchone()
        if item is None:
            raise KnowledgeCollectionError("Knowledge item not found.", status_code=404)
        connection.execute(
            """
            INSERT INTO knowledge_collection_items(knowledge_item_id, collection_id)
            VALUES (?, ?)
            ON CONFLICT(knowledge_item_id) DO UPDATE SET
                collection_id=excluded.collection_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (int(item_id), int(collection["id"])),
        )
    return {"knowledge_item_id": int(item_id), "collection": collection}


def app_collection_scope(app_id: int) -> dict[str, Any]:
    if int(app_id) < 1:
        raise KnowledgeCollectionError("Connected app not found.", status_code=404)
    with db() as connection:
        exists = connection.execute("SELECT id FROM paired_apps WHERE id=? LIMIT 1", (int(app_id),)).fetchone()
        if exists is None:
            raise KnowledgeCollectionError("Connected app not found.", status_code=404)
        rows = connection.execute(
            """
            SELECT c.collection_key, c.name
            FROM app_knowledge_collection_scopes s
            JOIN knowledge_collections c ON c.id=s.collection_id
            WHERE s.paired_app_id=?
            ORDER BY c.name COLLATE NOCASE, c.collection_key
            """,
            (int(app_id),),
        ).fetchall()
    return {
        "restricted": bool(rows),
        "collections": [str(row["collection_key"]) for row in rows],
        "collection_names": [str(row["name"]) for row in rows],
    }


def set_app_collection_scope(app_id: int, collection_keys: list[str]) -> dict[str, Any]:
    app_id = int(app_id)
    normalized: list[str] = []
    for raw in collection_keys[:64]:
        key = normalize_collection_key(raw)
        if key not in normalized:
            normalized.append(key)
    with db() as connection:
        app = connection.execute("SELECT id FROM paired_apps WHERE id=? LIMIT 1", (app_id,)).fetchone()
        if app is None:
            raise KnowledgeCollectionError("Connected app not found.", status_code=404)
        ids: list[int] = []
        for key in normalized:
            row = connection.execute(
                "SELECT id FROM knowledge_collections WHERE collection_key=? LIMIT 1", (key,)
            ).fetchone()
            if row is None:
                raise KnowledgeCollectionError(f"Knowledge collection not found: {key}", status_code=404)
            ids.append(int(row["id"]))
        connection.execute("DELETE FROM app_knowledge_collection_scopes WHERE paired_app_id=?", (app_id,))
        for collection_id in ids:
            connection.execute(
                "INSERT INTO app_knowledge_collection_scopes(paired_app_id, collection_id) VALUES (?, ?)",
                (app_id, collection_id),
            )
    return app_collection_scope(app_id)


def _allowed_collection_keys(app_id: int) -> set[str] | None:
    scope = app_collection_scope(app_id)
    if not scope["restricted"]:
        return None
    return {str(key) for key in scope["collections"]}


def _item_provenance(connection, item_id: int) -> dict[str, Any]:
    direct = connection.execute(
        """
        SELECT c.collection_key, c.name
        FROM knowledge_collection_items i
        JOIN knowledge_collections c ON c.id=i.collection_id
        WHERE i.knowledge_item_id=? LIMIT 1
        """,
        (int(item_id),),
    ).fetchone()
    if direct is not None:
        watched_metadata = connection.execute(
            """
            SELECT ksf.relative_path, ks.label
            FROM knowledge_source_files ksf
            JOIN knowledge_sources ks ON ks.id=ksf.source_id
            WHERE ksf.knowledge_item_id=?
            LIMIT 1
            """,
            (int(item_id),),
        ).fetchone()
        return {
            "collection_key": str(direct["collection_key"]),
            "collection_name": str(direct["name"]),
            "source_type": "watched_folder" if watched_metadata is not None else "local_item",
            "source_label": (
                str(watched_metadata["label"] or "Local folder")[:120]
                if watched_metadata is not None
                else "HomeServer Knowledge"
            ),
            "relative_path": (
                str(watched_metadata["relative_path"] or "")[:1000]
                if watched_metadata is not None
                else ""
            ),
        }

    watched = connection.execute(
        """
        SELECT ksf.relative_path, ks.label,
               COALESCE(c.collection_key, 'general') AS collection_key,
               COALESCE(c.name, 'General') AS collection_name
        FROM knowledge_source_files ksf
        JOIN knowledge_sources ks ON ks.id=ksf.source_id
        LEFT JOIN knowledge_collection_sources cs ON cs.source_id=ks.id
        LEFT JOIN knowledge_collections c ON c.id=cs.collection_id
        WHERE ksf.knowledge_item_id=?
        LIMIT 1
        """,
        (int(item_id),),
    ).fetchone()
    if watched is not None:
        return {
            "collection_key": str(watched["collection_key"] or DEFAULT_COLLECTION_KEY),
            "collection_name": str(watched["collection_name"] or "General"),
            "source_type": "watched_folder",
            "source_label": str(watched["label"] or "Local folder")[:120],
            "relative_path": str(watched["relative_path"] or "")[:1000],
        }

    return {
        "collection_key": DEFAULT_COLLECTION_KEY,
        "collection_name": "General",
        "source_type": "local_item",
        "source_label": "HomeServer Knowledge",
        "relative_path": "",
    }


def _fts_expression(query: str) -> str:
    tokens = [token for token in re.findall(r"\w+", str(query or ""), flags=re.UNICODE) if token]
    tokens = tokens[:12]
    return " AND ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)


def _citation(
    *,
    item_id: int,
    title: str,
    kind: str,
    content_hash: str,
    chunk_index: int,
    char_start: int,
    char_end: int,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    version = str(content_hash or "")[:16]
    reference = f"hs-knowledge:{item_id}:{chunk_index}:{version or 'unversioned'}"
    label_parts = [str(provenance["collection_name"])]
    if provenance.get("relative_path"):
        label_parts.append(str(provenance["relative_path"]))
    label_parts.append(f"chunk {chunk_index + 1}")
    return {
        "id": reference,
        "uri": f"homeserver://knowledge/{item_id}?chunk={chunk_index}&version={version or 'unversioned'}",
        "title": title,
        "kind": kind,
        "collection_key": provenance["collection_key"],
        "collection_name": provenance["collection_name"],
        "source_type": provenance["source_type"],
        "source_label": provenance["source_label"],
        "relative_path": provenance["relative_path"],
        "chunk_index": chunk_index,
        "char_start": char_start,
        "char_end": char_end,
        "version": version,
        "label": " · ".join(label_parts),
    }


def search_for_app(identity: dict[str, Any], query: str, limit: int = 20) -> dict[str, Any]:
    app_id = int(identity.get("id") or 0)
    if app_id < 1:
        raise KnowledgeCollectionError("Connected app identity is unavailable.", status_code=401)
    allowed = _allowed_collection_keys(app_id)
    q = str(query or "").strip()[:240]
    bounded = max(1, min(int(limit), 50))
    expression = _fts_expression(q)
    items: list[dict[str, Any]] = []

    with db() as connection:
        if expression:
            rows = connection.execute(
                """
                SELECT ki.id AS knowledge_item_id, ki.title, ki.kind, COALESCE(ki.content_hash, '') AS content_hash,
                       ki.updated_at, kc.chunk_index, kc.char_start, kc.char_end,
                       snippet(knowledge_chunks_fts, 0, '', '', ' … ', 40) AS snippet,
                       bm25(knowledge_chunks_fts) AS score
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks kc ON kc.id=CAST(knowledge_chunks_fts.chunk_id AS INTEGER)
                JOIN knowledge_items ki ON ki.id=kc.knowledge_item_id
                WHERE knowledge_chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (expression, max(100, bounded * 8)),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT ki.id AS knowledge_item_id, ki.title, ki.kind, COALESCE(ki.content_hash, '') AS content_hash,
                       ki.updated_at, 0 AS chunk_index, 0 AS char_start, 0 AS char_end,
                       '' AS snippet, 0.0 AS score
                FROM knowledge_items ki
                ORDER BY ki.updated_at DESC, ki.id DESC
                LIMIT ?
                """,
                (max(100, bounded * 8),),
            ).fetchall()

        seen: set[tuple[int, int]] = set()
        for row in rows:
            item_id = int(row["knowledge_item_id"])
            chunk_index = int(row["chunk_index"] or 0)
            identity_key = (item_id, chunk_index)
            if identity_key in seen:
                continue
            provenance = _item_provenance(connection, item_id)
            if allowed is not None and provenance["collection_key"] not in allowed:
                continue
            seen.add(identity_key)
            citation = _citation(
                item_id=item_id,
                title=str(row["title"] or "Knowledge item"),
                kind=str(row["kind"] or "note"),
                content_hash=str(row["content_hash"] or ""),
                chunk_index=chunk_index,
                char_start=int(row["char_start"] or 0),
                char_end=int(row["char_end"] or 0),
                provenance=provenance,
            )
            items.append(
                {
                    "id": item_id,
                    "title": str(row["title"] or ""),
                    "kind": str(row["kind"] or ""),
                    "snippet": str(row["snippet"] or ""),
                    "updated_at": row["updated_at"],
                    "score": float(row["score"] or 0.0),
                    "citation": citation,
                }
            )
            if len(items) >= bounded:
                break

    scope = app_collection_scope(app_id)
    return {
        "items": items,
        "query": q,
        "count": len(items),
        "scope": {"restricted": scope["restricted"], "collections": scope["collections"]},
        "privacy": {
            "local_only_index": True,
            "absolute_paths_exposed": False,
            "full_documents_returned": False,
        },
        "citation_version": "v0.37",
    }

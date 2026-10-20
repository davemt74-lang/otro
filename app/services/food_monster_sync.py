from __future__ import annotations

"""KB5B: owner-local, source-scoped Food Monster manifest index.

Live Food Monster remains authoritative. Each category is reconciled only
after one fully validated sweep. Two completed absent observations are
needed for a tombstone; incomplete sweeps never mutate cached documents.
No private Food Monster content is injected into universal Knowledge/Memories.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from ..database import db
from . import food_monster_connector as connector

REVISION = re.compile(r"^[a-f0-9]{64}$")
SOURCE_IDS = {
    "menu": re.compile(r"^gelato:menu:[1-9][0-9]{0,15}$"),
    "recipe": re.compile(r"^gelato:recipe:[A-Za-z0-9_.:-]{1,150}$"),
}
MAX_RECORDS_PER_CATEGORY = 2000
MAX_TEXT_BYTES = 32 * 1024
MAX_PAGES = 105


class FoodMonsterSyncCancelled(connector.FoodMonsterLinkError):
    """A canceled lease must not mutate cache or mark the source unavailable."""
    pass
CACHE_STALE_SECONDS = 3600


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _principal(local_id: str) -> tuple[dict[str, Any], str]:
    if not connector._ID.fullmatch(local_id):
        raise connector.FoodMonsterLinkError("Invalid connection ID.", 422)
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM food_monster_connections WHERE id=? AND status='active'",
            (local_id,),
        ).fetchone()
    if not row:
        raise connector.FoodMonsterLinkError("Food Monster connection is not active.", 404)
    entry = dict(row)
    scopes = json.loads(entry["scopes_json"])
    if not isinstance(scopes, list) or not scopes or any(t not in connector._TYPES for t in scopes):
        raise connector.FoodMonsterLinkError("Connection permissions are invalid.", 403)
    token = connector._vault_read().get(local_id)
    if not token:
        raise connector.FoodMonsterLinkError("Connection authorization is missing.", 403)
    # If the local approved scope set shrank, purge now-inaccessible
    # categories even when the source is offline. Never retain their data.
    placeholders = ",".join("?" for _ in scopes)
    with db() as connection:
        connection.execute(
            "DELETE FROM food_monster_documents WHERE connection_id=? "
            "AND kind NOT IN (" + placeholders + ")", (local_id, *scopes)
        )
        connection.execute(
            "DELETE FROM food_monster_sync_state WHERE connection_id=? "
            "AND kind NOT IN (" + placeholders + ")", (local_id, *scopes)
        )
    entry["scopes"] = scopes
    return entry, token


def _validate_document(raw: Any, kind: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise connector.FoodMonsterLinkError("Invalid knowledge record.", 502)
    source_id, revision, title, content = (
        raw.get("id"), raw.get("revision"), raw.get("title"), raw.get("content")
    )
    if (raw.get("type") != kind or not isinstance(source_id, str)
        or not SOURCE_IDS[kind].fullmatch(source_id)
        or not isinstance(revision, str) or not REVISION.fullmatch(revision)
        or not isinstance(title, str) or not title.strip() or len(title) > 250
        or not isinstance(content, dict)):
        raise connector.FoodMonsterLinkError("Knowledge record provenance is invalid.", 502)
    source = raw.get("source")
    if (not isinstance(source, dict)
        or source.get("app") != "food_monster"
        or source.get("kind") != ("active_menu_item" if kind == "menu" else "active_recipe")
        or not isinstance(source.get("id"), str)
        or source_id != "gelato:" + kind + ":" + source["id"]):
        raise connector.FoodMonsterLinkError("Knowledge source identity is invalid.", 502)
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_TEXT_BYTES:
        raise connector.FoodMonsterLinkError("Knowledge document is too large.", 502)
    updated_at = raw.get("updatedAt")
    if updated_at is not None and (not isinstance(updated_at, str) or len(updated_at) > 48):
        raise connector.FoodMonsterLinkError("Invalid source revision date.", 502)
    return {"source_id": source_id, "revision": revision, "title": title.strip(),
            "content_json": encoded, "search_text": encoded,
            "source_updated_at": updated_at}


def _pages(
    entry: dict[str, Any], token: str, kind: str,
    fetch: Callable[..., dict[str, Any]],
    guard: Callable[[], None] | None = None,
) -> dict[str, dict[str, Any]]:
    after = 0
    seen: dict[str, dict[str, Any]] = {}
    visited: set[int] = set()
    for _ in range(MAX_PAGES):
        if guard:
            guard()
        response = fetch(entry["origin"], {"action": "manifest",
            "type": kind, "afterId": after, "limit": 20}, token=token)
        if (response.get("schema") != "food-monster-knowledge-manifest-v1"
            or response.get("collection") != kind or response.get("readOnly") is not True
            or response.get("afterId") != after):
            raise connector.FoodMonsterLinkError("Manifest protocol or cursor mismatch.", 502)
        items = response.get("results")
        if not isinstance(items, list) or len(items) > 20 or response.get("count") != len(items):
            raise connector.FoodMonsterLinkError("Malformed or oversized manifest page.", 502)
        for item in items:
            doc = _validate_document(item, kind)
            if doc["source_id"] in seen:
                raise connector.FoodMonsterLinkError("Duplicate source record in manifest.", 502)
            seen[doc["source_id"]] = doc
            if len(seen) > MAX_RECORDS_PER_CATEGORY:
                raise connector.FoodMonsterLinkError("Manifest exceeds the approved sync limit.", 502)
        complete = response.get("complete")
        next_id = response.get("nextAfterId")
        if complete is True and next_id is None:
            return seen
        if (complete is not False or not isinstance(next_id, int)
            or isinstance(next_id, bool) or next_id <= after or not items
            or next_id in visited):
            raise connector.FoodMonsterLinkError("Manifest did not advance correctly.", 502)
        visited.add(next_id)
        after = next_id
    raise connector.FoodMonsterLinkError("Manifest page count exceeded safe limit.", 502)


def _apply_full_sweep(local_id: str, kind: str, docs: dict[str, dict[str, Any]], *, auto_lease: str | None = None) -> dict[str, int]:
    """Commit one category only after ALL pages were successfully validated."""
    now = _utc_now()
    changes = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0, "pendingRemoval": 0}
    with db() as connection:
        if auto_lease:
            from . import food_monster_auto_sync
            food_monster_auto_sync.assert_lease(local_id, auto_lease, connection=connection)
        # Lock write intent, and require still-active connection before writes.
        connection.execute("UPDATE food_monster_connections SET last_checked_at=? "
                           "WHERE id=? AND status='active'", (now, local_id))
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise connector.FoodMonsterLinkError("Connection was revoked during synchronization.", 403)
        state = connection.execute(
            "SELECT generation FROM food_monster_sync_state WHERE connection_id=? AND kind=?",
            (local_id,kind),
        ).fetchone()
        generation = (int(state["generation"]) if state else 0) + 1
        for doc in docs.values():
            original = connection.execute(
                "SELECT id,revision FROM food_monster_documents "
                "WHERE connection_id=? AND kind=? AND source_id=?",
                (local_id,kind,doc["source_id"]),
            ).fetchone()
            if original is None:
                connection.execute(
                    """INSERT INTO food_monster_documents
                    (connection_id,kind,source_id,revision,title,content_json,
                     search_text,source_updated_at,last_seen_generation,missing_complete_sweeps,indexed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,0,?)""",
                    (local_id,kind,doc["source_id"],doc["revision"],doc["title"],
                     doc["content_json"],doc["search_text"],doc["source_updated_at"],generation,now),
                )
                changes["created"] += 1
            elif original["revision"] != doc["revision"]:
                connection.execute(
                    """UPDATE food_monster_documents SET revision=?,title=?,content_json=?,
                    search_text=?,source_updated_at=?,last_seen_generation=?,
                    missing_complete_sweeps=0,indexed_at=?
                    WHERE id=?""",
                    (doc["revision"],doc["title"],doc["content_json"],
                     doc["search_text"],doc["source_updated_at"],generation,now,original["id"]),
                )
                changes["updated"] += 1
            else:
                connection.execute(
                    "UPDATE food_monster_documents SET last_seen_generation=?,"
                    "missing_complete_sweeps=0 WHERE id=?",
                    (generation,original["id"]),
                )
                changes["unchanged"] += 1
        connection.execute(
            """UPDATE food_monster_documents
            SET missing_complete_sweeps=missing_complete_sweeps+1
            WHERE connection_id=? AND kind=? AND last_seen_generation<>?""",
            (local_id,kind,generation),
        )
        changes["pendingRemoval"] = connection.execute(
            "SELECT COUNT(*) FROM food_monster_documents WHERE connection_id=? "
            "AND kind=? AND missing_complete_sweeps=1",(local_id,kind),
        ).fetchone()[0]
        changes["removed"] = connection.execute(
            "SELECT COUNT(*) FROM food_monster_documents WHERE connection_id=? "
            "AND kind=? AND missing_complete_sweeps>=2",(local_id,kind),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM food_monster_documents WHERE connection_id=? "
            "AND kind=? AND missing_complete_sweeps>=2",(local_id,kind),
        )
        connection.execute(
            """INSERT INTO food_monster_sync_state
            (connection_id,kind,generation,last_attempt_at,last_success_at,last_error)
            VALUES (?,?,?,?,?,'')
            ON CONFLICT(connection_id,kind) DO UPDATE SET
             generation=excluded.generation,last_attempt_at=excluded.last_attempt_at,
             last_success_at=excluded.last_success_at,last_error=''""",
            (local_id,kind,generation,now,now),
        )
    return changes


def _sync_failure(local_id: str, kind: str, error: connector.FoodMonsterLinkError) -> None:
    now = _utc_now()
    # Do not store exception content, untrusted HTTP bodies, tokens or recipe data.
    with db() as connection:
        if error.status in (401,403):
            connection.execute("DELETE FROM food_monster_documents WHERE connection_id=?", (local_id,))
            connection.execute("DELETE FROM food_monster_sync_state WHERE connection_id=?", (local_id,))
            connection.execute("UPDATE food_monster_connections SET status='disconnected',"
                               "last_error='Food Monster authorization was revoked.' WHERE id=?", (local_id,))
        else:
            connection.execute(
                """INSERT INTO food_monster_sync_state
                (connection_id,kind,last_attempt_at,last_error)
                VALUES (?,?,?,'Source unavailable; cached knowledge not verified.')
                ON CONFLICT(connection_id,kind) DO UPDATE SET
                 last_attempt_at=excluded.last_attempt_at,last_error=excluded.last_error""",
                (local_id,kind,now),
            )
    if error.status in (401,403):
        tokens = connector._vault_read()
        tokens.pop(local_id,None)
        connector._vault_write(tokens)


def synchronize(local_id: str, *, fetch: Callable[..., dict[str, Any]] | None = None,
                auto_lease: str | None = None) -> dict[str, Any]:
    """Manually trigger a bounded owner-only sweep, serialized with disconnect."""
    fetch = fetch or connector._request
    with connector._LOCK:
        entry, token = _principal(local_id)
        current_kind = sorted(entry["scopes"])[0]
        def check_lease() -> None:
            if auto_lease:
                from . import food_monster_auto_sync
                food_monster_auto_sync.assert_lease(local_id, auto_lease)
        try:
            check_lease()
            discovery = fetch(entry["origin"], None)
            if (discovery.get("schema") != "food-monster-vp3-v1"
                or str(discovery.get("instanceId") or "").lower() != entry["instance_id"]
                or "knowledge.manifest" not in (discovery.get("capabilities") or [])):
                raise connector.FoodMonsterLinkError("Food Monster instance or capability changed.", 502)
            outcomes = {}
            for kind in sorted(entry["scopes"]):
                current_kind = kind
                docs = _pages(entry,token,kind,fetch,guard=check_lease)
                outcomes[kind] = _apply_full_sweep(local_id,kind,docs,auto_lease=auto_lease)
            return {"connectionId":local_id, "instanceId":entry["instance_id"],
                    "origin":entry["origin"],"status":"synced","collections":outcomes,
                    "syncedAt":_utc_now()}
        except FoodMonsterSyncCancelled:
            raise
        except connector.FoodMonsterLinkError as error:
            _sync_failure(local_id, current_kind, error)
            raise


def purge(local_id: str, *, connection_provider=None) -> None:
    """Per-source private-cache destruction; FTS entries cascade via triggers."""
    # Connector callers may supply their owner-local test/transaction provider.
    # Production uses the exact same app.database.db connection factory.
    provider = connection_provider or db
    with provider() as connection:
        connection.execute("DELETE FROM food_monster_documents WHERE connection_id=?", (local_id,))
        connection.execute("DELETE FROM food_monster_sync_state WHERE connection_id=?", (local_id,))


def status(local_id: str) -> dict[str, Any]:
    entry, _ = _principal(local_id)
    with db() as connection:
        states = connection.execute(
            "SELECT kind,generation,last_attempt_at,last_success_at,last_error "
            "FROM food_monster_sync_state WHERE connection_id=?",(local_id,),
        ).fetchall()
        counts = connection.execute(
            "SELECT kind,COUNT(*) AS n FROM food_monster_documents "
            "WHERE connection_id=? GROUP BY kind",(local_id,),
        ).fetchall()
    by_kind = {row["kind"]:dict(row) for row in states}
    count_map = {row["kind"]:row["n"] for row in counts}
    return {"connectionId":local_id,"origin":entry["origin"],"instanceId":entry["instance_id"],
        "categories":{kind:{
            "documents":int(count_map.get(kind,0)),
            "lastSyncedAt":by_kind.get(kind,{}).get("last_success_at"),
            "lastAttemptAt":by_kind.get(kind,{}).get("last_attempt_at"),
            "lastError":by_kind.get(kind,{}).get("last_error",""),
            "completeGenerations":int(by_kind.get(kind,{}).get("generation") or 0),
        } for kind in entry["scopes"]}}


def query_cache(local_id: str, query: str,kind: str="all",limit: int=10) -> dict[str, Any]:
    """Explicit snapshot lookup. NEVER claim cached evidence is current/live."""
    if not isinstance(query,str) or not query.strip() or len(query)>160:
        raise connector.FoodMonsterLinkError("Search query must have 1–160 characters.")
    if kind not in connector._TYPES | {"all"} or not 1<=limit<=20:
        raise connector.FoodMonsterLinkError("Invalid cache category or limit.")
    entry,_ = _principal(local_id)
    scopes = entry["scopes"]
    if kind!="all" and kind not in scopes:
        raise connector.FoodMonsterLinkError("Data category is not authorized.",403)
    terms = re.findall(r"[\w]{2,}",query.casefold())[:8]
    if not terms:
        return {"results":[],"count":0,"freshness":"unverified_cached_snapshot"}
    expression = " OR ".join('"' + term.replace('"','') + '"' for term in terms)
    with db() as connection:
        sql = """SELECT d.kind,d.source_id,d.revision,d.title,d.content_json,d.source_updated_at,
                    s.last_success_at
                 FROM food_monster_documents_fts f
                 JOIN food_monster_documents d ON d.id=f.rowid
                 LEFT JOIN food_monster_sync_state s ON s.connection_id=d.connection_id AND s.kind=d.kind
                 WHERE food_monster_documents_fts MATCH ? AND d.connection_id=? """
        params: list[Any] = [expression,local_id]
        if kind!="all":
            sql+=" AND d.kind=?"
            params.append(kind)
        else:
            sql+=" AND d.kind IN (" + ",".join("?" for _ in scopes) + ")"
            params.extend(scopes)
        sql+=" ORDER BY bm25(food_monster_documents_fts) LIMIT ?"
        params.append(limit)
        rows=connection.execute(sql,params).fetchall()
    results=[{"id":row["source_id"],"kind":row["kind"],"title":row["title"],
              "revision":row["revision"],"content":json.loads(row["content_json"]),
              "sourceUpdatedAt":row["source_updated_at"],
              "cachedAt":row["last_success_at"],
              "origin":entry["origin"],"instanceId":entry["instance_id"],
              "freshness":"unverified_cached_snapshot"} for row in rows]
    return {"connectionId":local_id,"origin":entry["origin"],"instanceId":entry["instance_id"],
            "freshness":"unverified_cached_snapshot","liveVerified":False,
            "results":results,"count":len(results)}

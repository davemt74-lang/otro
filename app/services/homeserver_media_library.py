from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from . import homeserver_app_resources, homeserver_apps, homeserver_media_server

APP_KEY="vp3.media-library"
CONTRACT="vp3.media-library.v1"


class MediaLibraryError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _ensure_app()->dict[str,Any]:
    try:
        app=homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise MediaLibraryError("VP3 Media Library is not installed.",404) from exc
    if not app.get("installed_version"):
        raise MediaLibraryError("VP3 Media Library is not installed.",409)
    return app


def _connect()->sqlite3.Connection:
    _ensure_app()
    path=homeserver_app_resources.sqlite_path(APP_KEY,"media-library.db")
    connection=sqlite3.connect(path,timeout=20,check_same_thread=False)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_metadata(
            media_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            rating INTEGER CHECK(rating IS NULL OR (rating>=0 AND rating<=5)),
            favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
            taken_at TEXT,
            location_name TEXT NOT NULL DEFAULT '',
            custom_fields_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS media_metadata_tags(
            media_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY(media_id,tag)
        );
        CREATE TABLE IF NOT EXISTS media_metadata_relations(
            relation_id TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            relation_value TEXT NOT NULL,
            relation_label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(media_id,relation_type,relation_value)
        );
        CREATE TABLE IF NOT EXISTS media_metadata_history(
            history_id TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            source TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            reverted_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_media_metadata_history_media
          ON media_metadata_history(media_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS media_collections(
            collection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            collection_type TEXT NOT NULL DEFAULT 'manual',
            rules_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS media_collection_items(
            collection_id TEXT NOT NULL,
            media_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(collection_id,media_id),
            FOREIGN KEY(collection_id) REFERENCES media_collections(collection_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_media_collection_items_order
          ON media_collection_items(collection_id,position,created_at);
        """
    )
    return connection


def _canonical_media(media_id:str)->dict[str,Any]:
    try:
        return homeserver_media_server.item(str(media_id or ""))["item"]
    except homeserver_media_server.MediaServerError as exc:
        raise MediaLibraryError(str(exc),exc.status_code) from exc


def _normalize_tags(values:list[Any])->list[str]:
    out=[]
    for value in values[:100]:
        tag=" ".join(str(value or "").strip().lower().split())[:80]
        if tag and tag not in out:
            out.append(tag)
    return out


def _safe_custom(value:Any)->dict[str,Any]:
    if value is None:
        return {}
    if not isinstance(value,dict):
        raise MediaLibraryError("custom_fields must be an object.")
    if len(value)>100:
        raise MediaLibraryError("custom_fields may contain at most 100 keys.")
    out={}
    for key,val in value.items():
        name=" ".join(str(key or "").strip().split())[:80]
        if not name:
            continue
        if isinstance(val,(str,int,float,bool)) or val is None:
            out[name]=val
        elif isinstance(val,list) and len(val)<=50 and all(isinstance(x,(str,int,float,bool)) or x is None for x in val):
            out[name]=val
        else:
            raise MediaLibraryError(f"Unsupported custom field value for {name}.")
    return out


def _row_state(connection:sqlite3.Connection,media_id:str)->dict[str,Any]:
    canonical=_canonical_media(media_id)
    row=connection.execute("SELECT * FROM media_metadata WHERE media_id=?",(media_id,)).fetchone()
    tags=[str(r["tag"]) for r in connection.execute(
        "SELECT tag FROM media_metadata_tags WHERE media_id=? ORDER BY tag",(media_id,)
    ).fetchall()]
    relations=[
        {
            "relation_id":str(r["relation_id"]),
            "type":str(r["relation_type"]),
            "value":str(r["relation_value"]),
            "label":str(r["relation_label"]),
        }
        for r in connection.execute(
            "SELECT * FROM media_metadata_relations WHERE media_id=? ORDER BY relation_type,relation_label,relation_value",
            (media_id,),
        ).fetchall()
    ]
    meta=dict(row) if row else {}
    custom={}
    try:
        custom=json.loads(str(meta.get("custom_fields_json") or "{}"))
        if not isinstance(custom,dict):
            custom={}
    except json.JSONDecodeError:
        custom={}
    return {
        "media_id":media_id,
        "canonical":{
            "name":canonical["name"],
            "title":canonical["title"],
            "media_type":canonical["media_type"],
            "mime_type":canonical["mime_type"],
            "extension":canonical["extension"],
            "size_bytes":canonical["size_bytes"],
            "updated_at":canonical["updated_at"],
        },
        "metadata":{
            "title":str(meta.get("title") or ""),
            "description":str(meta.get("description") or ""),
            "rating":None if meta.get("rating") is None else int(meta["rating"]),
            "favorite":bool(meta.get("favorite",False)),
            "taken_at":meta.get("taken_at"),
            "location_name":str(meta.get("location_name") or ""),
            "custom_fields":custom,
            "tags":tags,
            "relations":relations,
            "updated_at":meta.get("updated_at"),
        },
        "source_file_modified":False,
        "source_file_deleted":False,
        "filesystem_path_exposed":False,
    }


def get(media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        state=_row_state(connection,str(media_id))
    finally:
        connection.close()
    return {"contract":CONTRACT,"item":state}


def _write_state(connection:sqlite3.Connection,media_id:str,state:dict[str,Any])->None:
    meta=dict(state.get("metadata") or {})
    custom=_safe_custom(meta.get("custom_fields") or {})
    rating=meta.get("rating")
    if rating is not None:
        rating=int(rating)
        if rating<0 or rating>5:
            raise MediaLibraryError("rating must be between 0 and 5.")
    connection.execute(
        """INSERT INTO media_metadata(
             media_id,title,description,rating,favorite,taken_at,location_name,custom_fields_json
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(media_id) DO UPDATE SET
             title=excluded.title,description=excluded.description,rating=excluded.rating,
             favorite=excluded.favorite,taken_at=excluded.taken_at,location_name=excluded.location_name,
             custom_fields_json=excluded.custom_fields_json,updated_at=CURRENT_TIMESTAMP""",
        (
            media_id,
            str(meta.get("title") or "")[:300],
            str(meta.get("description") or "")[:4000],
            rating,
            1 if bool(meta.get("favorite")) else 0,
            meta.get("taken_at"),
            str(meta.get("location_name") or "")[:300],
            json.dumps(custom,separators=(",",":"),ensure_ascii=False),
        ),
    )
    connection.execute("DELETE FROM media_metadata_tags WHERE media_id=?",(media_id,))
    for tag in _normalize_tags(list(meta.get("tags") or [])):
        connection.execute("INSERT INTO media_metadata_tags(media_id,tag) VALUES (?,?)",(media_id,tag))

    incoming_relations=list(meta.get("relations") or [])
    connection.execute("DELETE FROM media_metadata_relations WHERE media_id=?",(media_id,))
    for rel in incoming_relations[:200]:
        if not isinstance(rel,dict):
            continue
        relation_type=str(rel.get("type") or "").strip().lower()[:60]
        value=str(rel.get("value") or "").strip()[:300]
        label=str(rel.get("label") or "").strip()[:300]
        if relation_type not in {"person","artist","album","collection","location","other"} or not value:
            continue
        connection.execute(
            """INSERT OR IGNORE INTO media_metadata_relations(
                 relation_id,media_id,relation_type,relation_value,relation_label
               ) VALUES (?,?,?,?,?)""",
            ("rel_"+uuid.uuid4().hex,media_id,relation_type,value,label),
        )


def update(
    media_id:str,
    patch:dict[str,Any],
    *,
    actor:str="user",
    source:str="media-library",
    reason:str="",
)->dict[str,Any]:
    key=str(media_id or "")
    connection=_connect()
    try:
        before=_row_state(connection,key)
        next_state=json.loads(json.dumps(before))
        meta=next_state["metadata"]
        allowed={"title","description","rating","favorite","taken_at","location_name","custom_fields","tags","relations"}
        unknown=set(patch)-allowed
        if unknown:
            raise MediaLibraryError(f"Unknown metadata field: {sorted(unknown)[0]}")
        for field,value in patch.items():
            meta[field]=value
        _write_state(connection,key,next_state)
        after=_row_state(connection,key)
        history_id="hist_"+uuid.uuid4().hex
        connection.execute(
            """INSERT INTO media_metadata_history(
                 history_id,media_id,actor,source,reason,before_json,after_json
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                history_id,key,str(actor or "user")[:120],str(source or "media-library")[:120],
                str(reason or "")[:500],
                json.dumps(before,separators=(",",":"),ensure_ascii=False),
                json.dumps(after,separators=(",",":"),ensure_ascii=False),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "item":after,
        "history_id":history_id,
        "source_file_modified":False,
    }


def history(media_id:str,limit:int=100)->dict[str,Any]:
    _canonical_media(media_id)
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT history_id,media_id,actor,source,reason,reverted_by,created_at
               FROM media_metadata_history WHERE media_id=?
               ORDER BY created_at DESC,history_id DESC LIMIT ?""",
            (media_id,max(1,min(int(limit),500))),
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"history":[dict(row) for row in rows],"count":len(rows)}


def undo(history_id:str,*,actor:str="user",source:str="media-library.undo")->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute(
            "SELECT * FROM media_metadata_history WHERE history_id=?",(str(history_id),)
        ).fetchone()
        if not row:
            raise MediaLibraryError("Metadata history entry not found.",404)
        if row["reverted_by"]:
            raise MediaLibraryError("Metadata history entry was already reverted.",409)
        media_id=str(row["media_id"])
        current=_row_state(connection,media_id)
        before=json.loads(str(row["before_json"]))
        _write_state(connection,media_id,before)
        restored=_row_state(connection,media_id)
        new_id="hist_"+uuid.uuid4().hex
        connection.execute(
            """INSERT INTO media_metadata_history(
                 history_id,media_id,actor,source,reason,before_json,after_json
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                new_id,media_id,str(actor or "user")[:120],str(source or "media-library.undo")[:120],
                "Undo "+str(history_id),
                json.dumps(current,separators=(",",":"),ensure_ascii=False),
                json.dumps(restored,separators=(",",":"),ensure_ascii=False),
            ),
        )
        connection.execute(
            "UPDATE media_metadata_history SET reverted_by=? WHERE history_id=?",(new_id,history_id)
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "item":restored,
        "undo_history_id":new_id,
        "reverted_history_id":history_id,
        "source_file_modified":False,
    }


def search(
    query:str="",
    *,
    media_type:str="",
    tag:str="",
    favorite:bool|None=None,
    limit:int=200,
)->dict[str,Any]:
    q=" ".join(str(query or "").split()).lower()[:200]
    wanted_type=str(media_type or "").strip().lower()
    wanted_tag=" ".join(str(tag or "").split()).lower()[:80]
    source=homeserver_media_server.library(query="",media_type=wanted_type,limit=500,offset=0)
    items=[]
    connection=_connect()
    try:
        for row in source["items"]:
            state=_row_state(connection,str(row["media_id"]))
            meta=state["metadata"]
            hay=" ".join([
                str(state["canonical"]["title"]),
                str(meta["title"]),
                str(meta["description"]),
                " ".join(meta["tags"]),
                " ".join(str(r.get("label") or r.get("value") or "") for r in meta["relations"]),
            ]).lower()
            if q and q not in hay:
                continue
            if wanted_tag and wanted_tag not in meta["tags"]:
                continue
            if favorite is not None and bool(meta["favorite"])!=bool(favorite):
                continue
            items.append(state)
            if len(items)>=max(1,min(int(limit),500)):
                break
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "items":items,
        "count":len(items),
        "query":q,
        "media_type":wanted_type,
        "tag":wanted_tag,
        "favorite":favorite,
    }



def _validate_collection_rules(value:Any)->dict[str,Any]:
    if value is None:
        return {}
    if not isinstance(value,dict):
        raise MediaLibraryError("Collection rules must be an object.")
    allowed={"query","media_type","tag","favorite","rating_min"}
    unknown=set(value)-allowed
    if unknown:
        raise MediaLibraryError(f"Unknown collection rule: {sorted(unknown)[0]}")
    out={}
    if "query" in value:
        out["query"]=" ".join(str(value.get("query") or "").split())[:200]
    if "media_type" in value:
        kind=str(value.get("media_type") or "").strip().lower()
        if kind not in {"","video","audio","image"}:
            raise MediaLibraryError("Collection media_type rule is invalid.")
        out["media_type"]=kind
    if "tag" in value:
        out["tag"]=" ".join(str(value.get("tag") or "").lower().split())[:80]
    if "favorite" in value:
        fav=value.get("favorite")
        if fav is not None and not isinstance(fav,bool):
            raise MediaLibraryError("Collection favorite rule must be boolean or null.")
        out["favorite"]=fav
    if "rating_min" in value:
        rating=int(value.get("rating_min"))
        if rating<0 or rating>5:
            raise MediaLibraryError("Collection rating_min must be between 0 and 5.")
        out["rating_min"]=rating
    return out


def create_collection(
    name:str,
    *,
    description:str="",
    collection_type:str="manual",
    rules:dict[str,Any]|None=None,
)->dict[str,Any]:
    title=" ".join(str(name or "").split())[:160]
    if not title:
        raise MediaLibraryError("Collection name is required.")
    kind=str(collection_type or "manual").strip().lower()
    if kind not in {"manual","smart"}:
        raise MediaLibraryError("collection_type must be manual or smart.")
    normalized=_validate_collection_rules(rules or {})
    if kind=="smart" and not normalized:
        raise MediaLibraryError("Smart collections require at least one rule.")
    collection_id="collection_"+uuid.uuid4().hex
    connection=_connect()
    try:
        connection.execute(
            """INSERT INTO media_collections(
                 collection_id,name,description,collection_type,rules_json
               ) VALUES (?,?,?,?,?)""",
            (
                collection_id,title,str(description or "")[:2000],kind,
                json.dumps(normalized,separators=(",",":"),sort_keys=True),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_collection(collection_id)


def _collection_public(row:sqlite3.Row|dict[str,Any])->dict[str,Any]:
    data=dict(row)
    try:
        rules=json.loads(str(data.get("rules_json") or "{}"))
        if not isinstance(rules,dict):
            rules={}
    except json.JSONDecodeError:
        rules={}
    return {
        "collection_id":str(data["collection_id"]),
        "name":str(data["name"]),
        "description":str(data.get("description") or ""),
        "collection_type":str(data["collection_type"]),
        "rules":rules,
        "created_at":str(data["created_at"]),
        "updated_at":str(data["updated_at"]),
    }


def _smart_items(rules:dict[str,Any],limit:int=500)->list[dict[str,Any]]:
    result=search(
        str(rules.get("query") or ""),
        media_type=str(rules.get("media_type") or ""),
        tag=str(rules.get("tag") or ""),
        favorite=rules.get("favorite") if "favorite" in rules else None,
        limit=min(500,max(1,int(limit))),
    )
    rating_min=rules.get("rating_min")
    items=list(result["items"])
    if rating_min is not None:
        items=[
            row for row in items
            if row["metadata"].get("rating") is not None
            and int(row["metadata"]["rating"])>=int(rating_min)
        ]
    return items


def get_collection(collection_id:str,limit:int=500)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute(
            "SELECT * FROM media_collections WHERE collection_id=?",(str(collection_id),)
        ).fetchone()
        if not row:
            raise MediaLibraryError("Collection not found.",404)
        collection=_collection_public(row)
        if collection["collection_type"]=="manual":
            ids=[
                str(item["media_id"])
                for item in connection.execute(
                    """SELECT media_id FROM media_collection_items
                       WHERE collection_id=? ORDER BY position,created_at,media_id LIMIT ?""",
                    (collection_id,max(1,min(int(limit),500))),
                ).fetchall()
            ]
        else:
            ids=[]
    finally:
        connection.close()
    if collection["collection_type"]=="smart":
        items=_smart_items(collection["rules"],limit)
    else:
        items=[]
        for media_id in ids:
            try:
                items.append(get(media_id)["item"])
            except MediaLibraryError:
                continue
    return {
        "contract":CONTRACT,
        "collection":collection,
        "items":items,
        "count":len(items),
        "cross_media":True,
        "source_files_modified":False,
    }


def collections(limit:int=200)->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            "SELECT * FROM media_collections ORDER BY name COLLATE NOCASE,collection_id LIMIT ?",
            (max(1,min(int(limit),500)),),
        ).fetchall()
    finally:
        connection.close()
    out=[]
    for row in rows:
        item=_collection_public(row)
        try:
            item["count"]=get_collection(item["collection_id"],500)["count"]
        except MediaLibraryError:
            item["count"]=0
        out.append(item)
    return {"contract":CONTRACT,"collections":out,"count":len(out)}


def collection_add(collection_id:str,media_id:str,position:int=0)->dict[str,Any]:
    _canonical_media(media_id)
    connection=_connect()
    try:
        row=connection.execute(
            "SELECT collection_type FROM media_collections WHERE collection_id=?",(collection_id,)
        ).fetchone()
        if not row:
            raise MediaLibraryError("Collection not found.",404)
        if str(row["collection_type"])!="manual":
            raise MediaLibraryError("Items cannot be manually added to a smart collection.",409)
        connection.execute(
            """INSERT INTO media_collection_items(collection_id,media_id,position)
               VALUES (?,?,?)
               ON CONFLICT(collection_id,media_id) DO UPDATE SET
                 position=excluded.position""",
            (collection_id,media_id,max(-1_000_000,min(1_000_000,int(position)))),
        )
        connection.commit()
    finally:
        connection.close()
    return get_collection(collection_id)


def collection_remove(collection_id:str,media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute(
            "SELECT collection_type FROM media_collections WHERE collection_id=?",(collection_id,)
        ).fetchone()
        if not row:
            raise MediaLibraryError("Collection not found.",404)
        if str(row["collection_type"])!="manual":
            raise MediaLibraryError("Items cannot be manually removed from a smart collection.",409)
        connection.execute(
            "DELETE FROM media_collection_items WHERE collection_id=? AND media_id=?",
            (collection_id,media_id),
        )
        connection.commit()
    finally:
        connection.close()
    return get_collection(collection_id)


def delete_collection(collection_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        cur=connection.execute(
            "DELETE FROM media_collections WHERE collection_id=?",(str(collection_id),)
        )
        if cur.rowcount<1:
            raise MediaLibraryError("Collection not found.",404)
        connection.commit()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "deleted":True,
        "collection_id":collection_id,
        "media_files_deleted":False,
        "metadata_deleted":False,
    }


def smart_collections()->dict[str,Any]:
    rows=collections(500)["collections"]
    items=[row for row in rows if row["collection_type"]=="smart"]
    return {"contract":CONTRACT,"collections":items,"count":len(items)}


def status()->dict[str,Any]:
    connection=_connect()
    try:
        metadata_count=int(connection.execute("SELECT COUNT(*) FROM media_metadata").fetchone()[0])
        tags=int(connection.execute("SELECT COUNT(DISTINCT tag) FROM media_metadata_tags").fetchone()[0])
        relations=int(connection.execute("SELECT COUNT(*) FROM media_metadata_relations").fetchone()[0])
        history_count=int(connection.execute("SELECT COUNT(*) FROM media_metadata_history").fetchone()[0])
        collection_count=int(connection.execute("SELECT COUNT(*) FROM media_collections").fetchone()[0])
        smart_count=int(connection.execute("SELECT COUNT(*) FROM media_collections WHERE collection_type='smart'").fetchone()[0])
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "metadata_items":metadata_count,
        "tags":tags,
        "relations":relations,
        "history_entries":history_count,
        "collections":collection_count,
        "smart_collections":smart_count,
        "canonical_source":"vp3.media-server",
        "source_files_modified":False,
        "source_files_deleted":False,
        "history_undo":True,
        "custom_fields":True,
        "provenance":True,
        "manual_collections":True,
        "smart_collections":True,
        "saved_filters":True,
        "cross_media_collections":True,
        "homeserver_execution_authority":True,
    }


def brain_context(limit:int=8)->dict[str,Any]:
    state=status()
    source=homeserver_media_server.library(limit=max(1,min(int(limit),20)))
    recent=[]
    for row in source["items"][:max(1,min(int(limit),20))]:
        try:
            item_state=get(str(row["media_id"]))["item"]
        except Exception:
            continue
        meta=item_state["metadata"]
        recent.append({
            "media_id":item_state["media_id"],
            "media_type":item_state["canonical"]["media_type"],
            "title":meta["title"] or item_state["canonical"]["title"],
            "favorite":meta["favorite"],
            "rating":meta["rating"],
            "tags":meta["tags"][:8],
        })
    return {
        "contract":"vp3.media-library.brain-context.v1",
        "summary":{
            "metadata_items":state["metadata_items"],
            "tags":state["tags"],
            "relations":state["relations"],
            "history_entries":state["history_entries"],
            "collections":state["collections"],
            "smart_collections":state["smart_collections"],
        },
        "recent":recent,
        "source_files_modified":False,
        "filesystem_paths_exposed":False,
    }


def invoke(action:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    key=str(action or "").strip()
    if key=="library.status":
        return status()
    if key=="library.item.get":
        return get(str(args.get("media_id") or ""))
    if key=="library.item.update":
        return update(
            str(args.get("media_id") or ""),
            dict(args.get("patch") or {}),
            actor=str(args.get("actor") or "agent"),
            source=str(args.get("source") or "agent"),
            reason=str(args.get("reason") or ""),
        )
    if key=="library.history":
        return history(str(args.get("media_id") or ""),int(args.get("limit",100)))
    if key=="library.undo":
        return undo(str(args.get("history_id") or ""),actor=str(args.get("actor") or "agent"))
    if key=="library.search":
        favorite=args.get("favorite")
        return search(
            str(args.get("query") or ""),
            media_type=str(args.get("media_type") or ""),
            tag=str(args.get("tag") or ""),
            favorite=None if favorite is None else bool(favorite),
            limit=int(args.get("limit",200)),
        )
    if key=="library.collections":
        return collections(int(args.get("limit",200)))
    if key=="library.collection.get":
        return get_collection(str(args.get("collection_id") or ""),int(args.get("limit",500)))
    if key=="library.collection.create":
        return create_collection(
            str(args.get("name") or ""),
            description=str(args.get("description") or ""),
            collection_type=str(args.get("collection_type") or "manual"),
            rules=dict(args.get("rules") or {}),
        )
    if key=="library.collection.add":
        return collection_add(
            str(args.get("collection_id") or ""),
            str(args.get("media_id") or ""),
            int(args.get("position",0)),
        )
    if key=="library.collection.remove":
        return collection_remove(
            str(args.get("collection_id") or ""),
            str(args.get("media_id") or ""),
        )
    if key=="library.collection.delete":
        return delete_collection(str(args.get("collection_id") or ""))
    if key=="library.smart-collections":
        return smart_collections()
    if key=="library.brain-context":
        return brain_context(int(args.get("limit",8)))
    raise MediaLibraryError("Unsupported Media Library action.",404)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "canonical_media_ids":True,
        "shared_metadata_authority":True,
        "tags":True,
        "ratings":True,
        "favorites":True,
        "people_artist_album_relations":True,
        "custom_fields":True,
        "metadata_history":True,
        "undo":True,
        "provenance":True,
        "manual_collections":True,
        "smart_collections":True,
        "saved_filters":True,
        "cross_media_collections":True,
        "source_file_writes":False,
        "source_file_deletes":False,
        "agent_brain_context":True,
        "universal_agent_control":True,
        "homeserver_execution_authority":True,
    }

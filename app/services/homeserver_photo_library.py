from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from . import homeserver_app_resources, homeserver_apps, homeserver_media_server

APP_KEY="vp3.photo-library"
CONTRACT="vp3.photo-library.v1"


class PhotoLibraryError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _ensure_app()->dict[str,Any]:
    try:
        app=homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise PhotoLibraryError("VP3 Photo Library is not installed.",404) from exc
    if not app.get("installed_version"):
        raise PhotoLibraryError("VP3 Photo Library is not installed.",409)
    return app


def _connect()->sqlite3.Connection:
    _ensure_app()
    path=homeserver_app_resources.sqlite_path(APP_KEY,"photo-library.db")
    connection=sqlite3.connect(path,timeout=15)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS photo_items(
            media_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            root_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            mime_type TEXT NOT NULL DEFAULT '',
            extension TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            mtime_ns INTEGER NOT NULL DEFAULT 0,
            source_created_at TEXT NOT NULL DEFAULT '',
            source_updated_at TEXT NOT NULL DEFAULT '',
            source_file_date TEXT NOT NULL DEFAULT '',
            folder_album TEXT NOT NULL DEFAULT '',
            sync_generation INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_photo_items_folder ON photo_items(folder_album,title);
        CREATE INDEX IF NOT EXISTS idx_photo_items_updated ON photo_items(source_updated_at DESC);
        CREATE TABLE IF NOT EXISTS photo_favorites(
            media_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS photo_albums(
            album_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS photo_album_items(
            album_id TEXT NOT NULL,
            media_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(album_id,media_id),
            FOREIGN KEY(album_id) REFERENCES photo_albums(album_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_photo_album_position ON photo_album_items(album_id,position);
        CREATE TABLE IF NOT EXISTS photo_tags(
            tag TEXT NOT NULL,
            media_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(tag,media_id)
        );
        CREATE INDEX IF NOT EXISTS idx_photo_tags_media ON photo_tags(media_id,tag);
        CREATE TABLE IF NOT EXISTS photo_people(
            person_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS photo_person_items(
            person_id TEXT NOT NULL,
            media_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(person_id,media_id),
            FOREIGN KEY(person_id) REFERENCES photo_people(person_id) ON DELETE CASCADE
        );
        """
    )
    columns={str(row["name"]) for row in connection.execute("PRAGMA table_info(photo_items)").fetchall()}
    if "source_file_date" not in columns:
        connection.execute("ALTER TABLE photo_items ADD COLUMN source_file_date TEXT NOT NULL DEFAULT ''")
        connection.commit()
    return connection


def _source_file_date(mtime_ns:int)->str:
    try:
        return datetime.fromtimestamp(max(0,int(mtime_ns))/1_000_000_000,tz=timezone.utc).date().isoformat()
    except (OSError,OverflowError,ValueError):
        return ""


def _folder_album(relative_path:str)->str:
    parts=list(PurePosixPath(str(relative_path or "")).parts)
    if len(parts)>=2:
        return parts[-2][:240]
    return "Unsorted"


def sync()->dict[str,Any]:
    _ensure_app()
    try:
        records=homeserver_media_server.image_source_records()
    except Exception as exc:
        raise PhotoLibraryError("Media Server is unavailable. Install it, map a photo folder, and scan first.",409) from exc
    connection=_connect()
    try:
        generation=int(connection.execute("SELECT COALESCE(MAX(sync_generation),0)+1 FROM photo_items").fetchone()[0])
        for row in records:
            media_id=str(row["media_id"])
            connection.execute(
                """INSERT INTO photo_items(
                    media_id,title,root_id,relative_path,mime_type,extension,size_bytes,mtime_ns,
                    source_created_at,source_updated_at,source_file_date,folder_album,sync_generation
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(media_id) DO UPDATE SET
                    title=excluded.title,root_id=excluded.root_id,relative_path=excluded.relative_path,
                    mime_type=excluded.mime_type,extension=excluded.extension,size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,source_created_at=excluded.source_created_at,
                    source_updated_at=excluded.source_updated_at,source_file_date=excluded.source_file_date,
                    folder_album=excluded.folder_album,sync_generation=excluded.sync_generation,indexed_at=CURRENT_TIMESTAMP""",
                (
                    media_id,str(row.get("title") or "")[:300],str(row.get("root_id") or ""),
                    str(row.get("relative_path") or ""),str(row.get("mime_type") or ""),
                    str(row.get("extension") or ""),int(row.get("size_bytes") or 0),int(row.get("mtime_ns") or 0),
                    str(row.get("created_at") or ""),str(row.get("updated_at") or ""),
                    _source_file_date(int(row.get("mtime_ns") or 0)),
                    _folder_album(str(row.get("relative_path") or "")),generation,
                ),
            )
        connection.execute("DELETE FROM photo_items WHERE sync_generation<>?",(generation,))
        connection.execute("DELETE FROM photo_favorites WHERE media_id NOT IN (SELECT media_id FROM photo_items)")
        connection.execute("DELETE FROM photo_album_items WHERE media_id NOT IN (SELECT media_id FROM photo_items)")
        connection.execute("DELETE FROM photo_tags WHERE media_id NOT IN (SELECT media_id FROM photo_items)")
        connection.execute("DELETE FROM photo_person_items WHERE media_id NOT IN (SELECT media_id FROM photo_items)")
        connection.commit()
        total=int(connection.execute("SELECT COUNT(*) FROM photo_items").fetchone()[0])
        folders=int(connection.execute("SELECT COUNT(DISTINCT folder_album) FROM photo_items").fetchone()[0])
    finally:
        connection.close()
    return {"contract":CONTRACT,"photos":total,"folder_albums":folders,"source":"vp3.media-server"}


def _public_photo(row:sqlite3.Row|dict[str,Any], *, favorite:bool=False, tags:list[str]|None=None)->dict[str,Any]:
    mtime_ns=int(row["mtime_ns"])
    modified_at=datetime.fromtimestamp(mtime_ns/1_000_000_000,tz=timezone.utc).isoformat() if mtime_ns>0 else None
    return {
        "media_id":str(row["media_id"]),
        "title":str(row["title"]),
        "folder_album":str(row["folder_album"]),
        "mime_type":str(row["mime_type"]),
        "extension":str(row["extension"]),
        "size_bytes":int(row["size_bytes"]),
        "source_created_at":str(row["source_created_at"]),
        "source_updated_at":str(row["source_updated_at"]),
        "source_file_date":str(row["source_file_date"]),
        "file_modified_at":modified_at,
        "favorite":bool(favorite),
        "tags":list(tags or []),
        "view_url":f"/api/v1/control/homeserver-apps/media-server/stream/{row['media_id']}",
        "source_owned_by_photo_library":False,
        "absolute_path_exposed":False,
    }


def photos(query:str="",folder_album:str="",tag:str="",favorites_only:bool=False,limit:int=200,offset:int=0)->dict[str,Any]:
    q=" ".join(str(query or "").split())[:200]
    clauses=[];params=[]
    if q:
        clauses.append("(LOWER(p.title) LIKE ? ESCAPE '\\' OR LOWER(p.folder_album) LIKE ? ESCAPE '\\')")
        term="%"+q.lower().replace("%","\\%").replace("_","\\_")+"%"
        params.extend([term,term])
    if folder_album:
        clauses.append("p.folder_album=?");params.append(str(folder_album)[:240])
    if tag:
        clauses.append("EXISTS (SELECT 1 FROM photo_tags t WHERE t.media_id=p.media_id AND t.tag=?)")
        params.append(str(tag)[:80].lower())
    if favorites_only:
        clauses.append("EXISTS (SELECT 1 FROM photo_favorites f WHERE f.media_id=p.media_id)")
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    bounded=max(1,min(int(limit),500))
    skip=max(0,min(int(offset),1_000_000))
    connection=_connect()
    try:
        rows=connection.execute(
            "SELECT p.* FROM photo_items p"+where+" ORDER BY p.source_updated_at DESC,p.media_id LIMIT ? OFFSET ?",
            (*params,bounded,skip),
        ).fetchall()
        total=int(connection.execute("SELECT COUNT(*) FROM photo_items p"+where,tuple(params)).fetchone()[0])
        favs={str(row["media_id"]) for row in connection.execute("SELECT media_id FROM photo_favorites").fetchall()}
        tag_rows=connection.execute("SELECT media_id,tag FROM photo_tags ORDER BY tag").fetchall()
        tag_map=defaultdict(list)
        for row in tag_rows:
            tag_map[str(row["media_id"])].append(str(row["tag"]))
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "photos":[_public_photo(row,favorite=str(row["media_id"]) in favs,tags=tag_map.get(str(row["media_id"]),[])) for row in rows],
        "count":len(rows),"total":total,"offset":skip
    }


def folders()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            "SELECT folder_album,COUNT(*) photos,MAX(source_updated_at) updated_at FROM photo_items GROUP BY folder_album ORDER BY folder_album COLLATE NOCASE"
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"folders":[dict(row) for row in rows],"count":len(rows)}


def timeline(limit:int=500)->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT date(mtime_ns / 1000000000, 'unixepoch') day,COUNT(*) photos
               FROM photo_items WHERE mtime_ns>0 GROUP BY day ORDER BY day DESC LIMIT ?""",
            (max(1,min(int(limit),2000)),),
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"days":[dict(row) for row in rows],"count":len(rows)}


def favorite(media_id:str,enabled:bool=True)->dict[str,Any]:
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM photo_items WHERE media_id=?",(media_id,)).fetchone():
            raise PhotoLibraryError("Photo not found.",404)
        if enabled:
            connection.execute("INSERT OR IGNORE INTO photo_favorites(media_id) VALUES (?)",(media_id,))
        else:
            connection.execute("DELETE FROM photo_favorites WHERE media_id=?",(media_id,))
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"media_id":media_id,"favorite":bool(enabled)}


def set_tags(media_id:str,tags:list[str])->dict[str,Any]:
    cleaned=[]
    for raw in tags[:50]:
        value=" ".join(str(raw or "").split()).lower()[:80]
        if value and value not in cleaned:
            cleaned.append(value)
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM photo_items WHERE media_id=?",(media_id,)).fetchone():
            raise PhotoLibraryError("Photo not found.",404)
        connection.execute("DELETE FROM photo_tags WHERE media_id=?",(media_id,))
        for tag in cleaned:
            connection.execute("INSERT INTO photo_tags(tag,media_id) VALUES (?,?)",(tag,media_id))
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"media_id":media_id,"tags":cleaned}


def tags()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            "SELECT tag,COUNT(*) photos FROM photo_tags GROUP BY tag ORDER BY tag COLLATE NOCASE"
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"tags":[dict(row) for row in rows],"count":len(rows)}


def create_album(name:str)->dict[str,Any]:
    label=" ".join(str(name or "").split())[:160]
    if not label:
        raise PhotoLibraryError("Album name is required.")
    album_id="album_"+uuid.uuid4().hex
    connection=_connect()
    try:
        connection.execute("INSERT INTO photo_albums(album_id,name) VALUES (?,?)",(album_id,label))
        connection.commit()
    finally:
        connection.close()
    return album(album_id)


def albums()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT a.*,COUNT(i.media_id) photos FROM photo_albums a
               LEFT JOIN photo_album_items i ON i.album_id=a.album_id
               GROUP BY a.album_id ORDER BY a.updated_at DESC"""
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"albums":[dict(row) for row in rows],"count":len(rows)}


def album(album_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM photo_albums WHERE album_id=?",(album_id,)).fetchone()
        if not row:
            raise PhotoLibraryError("Album not found.",404)
        items=connection.execute(
            """SELECT p.* FROM photo_album_items i JOIN photo_items p ON p.media_id=i.media_id
               WHERE i.album_id=? ORDER BY i.position,p.title""",(album_id,)
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"album":dict(row),"photos":[_public_photo(x) for x in items]}


def album_add(album_id:str,media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM photo_albums WHERE album_id=?",(album_id,)).fetchone():
            raise PhotoLibraryError("Album not found.",404)
        if not connection.execute("SELECT 1 FROM photo_items WHERE media_id=?",(media_id,)).fetchone():
            raise PhotoLibraryError("Photo not found.",404)
        pos=int(connection.execute("SELECT COALESCE(MAX(position),-1)+1 FROM photo_album_items WHERE album_id=?",(album_id,)).fetchone()[0])
        connection.execute(
            """INSERT INTO photo_album_items(album_id,media_id,position) VALUES (?,?,?)
               ON CONFLICT(album_id,media_id) DO UPDATE SET position=excluded.position""",
            (album_id,media_id,pos),
        )
        connection.execute("UPDATE photo_albums SET updated_at=CURRENT_TIMESTAMP WHERE album_id=?",(album_id,))
        connection.commit()
    finally:
        connection.close()
    return album(album_id)


def album_remove(album_id:str,media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        connection.execute("DELETE FROM photo_album_items WHERE album_id=? AND media_id=?",(album_id,media_id))
        connection.execute("UPDATE photo_albums SET updated_at=CURRENT_TIMESTAMP WHERE album_id=?",(album_id,))
        connection.commit()
    finally:
        connection.close()
    return album(album_id)


def delete_album(album_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        cur=connection.execute("DELETE FROM photo_albums WHERE album_id=?",(album_id,))
        if cur.rowcount<1:
            raise PhotoLibraryError("Album not found.",404)
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"deleted":True,"album_id":album_id,"source_files_deleted":False}


def create_person(name:str)->dict[str,Any]:
    label=" ".join(str(name or "").split())[:160]
    if not label:
        raise PhotoLibraryError("Person name is required.")
    person_id="person_"+uuid.uuid4().hex
    connection=_connect()
    try:
        connection.execute("INSERT INTO photo_people(person_id,name) VALUES (?,?)",(person_id,label))
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"person_id":person_id,"name":label,"recognition_enabled":False}


def people()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT p.person_id,p.name,COUNT(i.media_id) photos
               FROM photo_people p LEFT JOIN photo_person_items i ON i.person_id=p.person_id
               GROUP BY p.person_id ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"people":[{**dict(row),"recognition_enabled":False} for row in rows],"count":len(rows)}


def person_assign(person_id:str,media_id:str,enabled:bool=True)->dict[str,Any]:
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM photo_people WHERE person_id=?",(person_id,)).fetchone():
            raise PhotoLibraryError("Person not found.",404)
        if not connection.execute("SELECT 1 FROM photo_items WHERE media_id=?",(media_id,)).fetchone():
            raise PhotoLibraryError("Photo not found.",404)
        if enabled:
            connection.execute("INSERT OR IGNORE INTO photo_person_items(person_id,media_id) VALUES (?,?)",(person_id,media_id))
        else:
            connection.execute("DELETE FROM photo_person_items WHERE person_id=? AND media_id=?",(person_id,media_id))
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"person_id":person_id,"media_id":media_id,"assigned":bool(enabled),"recognition_enabled":False}


def duplicate_groups(limit:int=100)->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT size_bytes,extension,COUNT(*) copies
               FROM photo_items
               WHERE size_bytes>0
               GROUP BY size_bytes,extension
               HAVING COUNT(*)>1
               ORDER BY copies DESC,size_bytes DESC LIMIT ?""",
            (max(1,min(int(limit),500)),),
        ).fetchall()
        groups=[]
        for group in rows:
            items=connection.execute(
                """SELECT * FROM photo_items WHERE size_bytes=? AND extension=?
                   ORDER BY title COLLATE NOCASE,media_id""",
                (int(group["size_bytes"]),str(group["extension"])),
            ).fetchall()
            groups.append({
                "signature":f"{group['size_bytes']}:{group['extension']}",
                "copies":len(items),
                "candidate_only":True,
                "photos":[_public_photo(item) for item in items],
            })
    finally:
        connection.close()
    return {"contract":CONTRACT,"groups":groups,"count":len(groups),"content_hash_verified":False}


def smart_albums()->dict[str,Any]:
    connection=_connect()
    try:
        favorites_count=int(connection.execute("SELECT COUNT(*) FROM photo_favorites").fetchone()[0])
        recent_count=int(connection.execute(
            "SELECT COUNT(*) FROM photo_items WHERE source_created_at>=datetime('now','-30 days')"
        ).fetchone()[0])
        screenshots=int(connection.execute(
            "SELECT COUNT(*) FROM photo_items WHERE LOWER(title) LIKE '%screenshot%'"
        ).fetchone()[0])
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "smart_albums":[
            {"key":"favorites","name":"Favorites","photos":favorites_count},
            {"key":"recent","name":"Recently Added","photos":recent_count},
            {"key":"screenshots","name":"Screenshots","photos":screenshots},
        ],
    }


def slideshow(query:str="",folder_album:str="",limit:int=200)->dict[str,Any]:
    result=photos(query=query,folder_album=folder_album,limit=limit)
    return {"contract":CONTRACT,"slides":result["photos"],"count":result["count"],"source_media_owned_by_photo_library":False}


def status()->dict[str,Any]:
    connection=_connect()
    try:
        total=int(connection.execute("SELECT COUNT(*) FROM photo_items").fetchone()[0])
        favorites_count=int(connection.execute("SELECT COUNT(*) FROM photo_favorites").fetchone()[0])
        albums_count=int(connection.execute("SELECT COUNT(*) FROM photo_albums").fetchone()[0])
        tags_count=int(connection.execute("SELECT COUNT(DISTINCT tag) FROM photo_tags").fetchone()[0])
        people_count=int(connection.execute("SELECT COUNT(*) FROM photo_people").fetchone()[0])
    finally:
        connection.close()
    try:
        roots=homeserver_media_server.roots()
    except Exception:
        roots={"count":0,"mapped_sources":0}
    return {
        "contract":CONTRACT,"app_key":APP_KEY,"photos":total,"favorites":favorites_count,
        "albums":albums_count,"tags":tags_count,"people":people_count,
        "media_roots":int(roots.get("count") or 0),"mapped_sources":int(roots.get("mapped_sources") or 0),
        "source":"vp3.media-server","source_media_owned_by_photo_library":False,
        "face_recognition_enabled":False,
    }


def invoke(action:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    key=str(action or "").strip()
    if key=="photos.status": return status()
    if key=="photos.sync": return sync()
    if key=="photos.search": return photos(
        query=str(args.get("query") or ""),folder_album=str(args.get("folder_album") or ""),
        tag=str(args.get("tag") or ""),favorites_only=bool(args.get("favorites_only",False)),
        limit=int(args.get("limit",200)),
    )
    if key=="photos.folders": return folders()
    if key=="photos.timeline": return timeline(int(args.get("limit",500)))
    if key=="photos.favorite.set": return favorite(str(args.get("media_id") or ""),bool(args.get("enabled",True)))
    if key=="photos.tags": return tags()
    if key=="photos.tags.set": return set_tags(str(args.get("media_id") or ""),list(args.get("tags") or []))
    if key=="photos.albums": return albums()
    if key=="photos.album.create": return create_album(str(args.get("name") or ""))
    if key=="photos.album.add": return album_add(str(args.get("album_id") or ""),str(args.get("media_id") or ""))
    if key=="photos.album.remove": return album_remove(str(args.get("album_id") or ""),str(args.get("media_id") or ""))
    if key=="photos.album.delete": return delete_album(str(args.get("album_id") or ""))
    if key=="photos.people": return people()
    if key=="photos.person.create": return create_person(str(args.get("name") or ""))
    if key=="photos.person.assign": return person_assign(
        str(args.get("person_id") or ""),str(args.get("media_id") or ""),bool(args.get("enabled",True))
    )
    if key=="photos.duplicates": return duplicate_groups(int(args.get("limit",100)))
    if key=="photos.smart-albums": return smart_albums()
    if key=="photos.slideshow": return slideshow(
        query=str(args.get("query") or ""),folder_album=str(args.get("folder_album") or ""),limit=int(args.get("limit",200))
    )
    raise PhotoLibraryError("Unsupported Photo Library action.",404)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "media_server_source":True,
        "mapped_computer_folders":True,
        "timeline":True,
        "albums":True,
        "folder_albums":True,
        "favorites":True,
        "tags":True,
        "smart_albums":True,
        "duplicate_candidates":True,
        "people_placeholders":True,
        "face_recognition_enabled":False,
        "slideshow":True,
        "private_hosted_viewing":True,
        "source_media_owned_by_photo_library":False,
        "universal_agent_control":True,
    }

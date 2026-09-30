from __future__ import annotations

import hashlib
import hmac
import time
import mimetypes
import os
import secrets
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from . import homeserver_app_resources, homeserver_app_security, homeserver_apps

APP_KEY="vp3.media-server"
CONTRACT="vp3.media-server.v1"
MAX_LIBRARY_FILES=100_000
VIDEO_EXT={".mp4",".m4v",".mov",".webm",".mkv",".avi"}
AUDIO_EXT={".mp3",".m4a",".aac",".flac",".wav",".ogg",".opus"}
IMAGE_EXT={".jpg",".jpeg",".png",".gif",".webp",".bmp",".tif",".tiff"}
MEDIA_EXT=VIDEO_EXT|AUDIO_EXT|IMAGE_EXT


class MediaServerError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _ensure_app()->dict[str,Any]:
    try:
        app=homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise MediaServerError("VP3 Media Server is not installed.",404) from exc
    if not app.get("installed_version"):
        raise MediaServerError("VP3 Media Server is not installed.",409)
    return app


def _require_files_permission()->None:
    status=homeserver_app_security.permission_status(APP_KEY)
    row=next((r for r in status.get("permissions",[]) if r.get("permission")=="files.read"),None)
    if not row or not row.get("allowed"):
        raise MediaServerError("Media Server requires the files.read permission before media folders can be scanned.",403)


def _connect()->sqlite3.Connection:
    _ensure_app()
    path=homeserver_app_resources.sqlite_path(APP_KEY,"media-server.db")
    connection=sqlite3.connect(path,timeout=15)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_roots(
            root_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            root_path TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS media_items(
            media_id TEXT PRIMARY KEY,
            root_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            title TEXT NOT NULL,
            media_type TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            extension TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            scan_generation INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(root_id,relative_path),
            FOREIGN KEY(root_id) REFERENCES media_roots(root_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_media_items_type_title ON media_items(media_type,title);
        CREATE INDEX IF NOT EXISTS idx_media_items_root ON media_items(root_id,relative_path);
        CREATE TABLE IF NOT EXISTS media_playback(
            media_id TEXT PRIMARY KEY,
            position_seconds REAL NOT NULL DEFAULT 0,
            duration_seconds REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(media_id) REFERENCES media_items(media_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS media_settings(
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS media_mapped_sources(
            root_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL DEFAULT 'local_folder',
            computer_name TEXT NOT NULL DEFAULT '',
            source_hint TEXT NOT NULL DEFAULT '',
            connected INTEGER NOT NULL DEFAULT 1 CHECK(connected IN (0,1)),
            last_checked_at TEXT,
            FOREIGN KEY(root_id) REFERENCES media_roots(root_id) ON DELETE CASCADE
        );
        """
    )
    return connection


def _root_id(path:Path)->str:
    return "medroot_"+hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]


def _media_id(root_id:str,relative_path:str)->str:
    return "media_"+hashlib.sha256((root_id+"\n"+relative_path).encode("utf-8")).hexdigest()[:32]


def _validate_root(path_value:str)->Path:
    raw=str(path_value or "").strip()
    if not raw:
        raise MediaServerError("Media folder path is required.")
    path=Path(raw).expanduser()
    try:
        resolved=path.resolve(strict=True)
    except (OSError,RuntimeError) as exc:
        raise MediaServerError("Media folder does not exist or cannot be resolved.",404) from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise MediaServerError("Media folder must be a real directory and may not be a symbolic link.")
    if resolved.parent==resolved:
        raise MediaServerError("A filesystem root cannot be granted as a Media Server library.",409)
    return resolved


def _public_root(row:sqlite3.Row|dict[str,Any], mapping:dict[str,Any]|None=None)->dict[str,Any]:
    mapping=dict(mapping or {})
    return {
        "root_id":str(row["root_id"]),
        "label":str(row["label"]),
        "enabled":bool(row["enabled"]),
        "source_kind":str(mapping.get("source_kind") or "local_folder"),
        "computer_name":str(mapping.get("computer_name") or ""),
        "source_hint_configured":bool(str(mapping.get("source_hint") or "")),
        "connected":bool(mapping.get("connected",True)),
        "absolute_path_exposed":False,
    }


def add_root(path_value:str,label:str="")->dict[str,Any]:
    _ensure_app()
    _require_files_permission()
    root=_validate_root(path_value)
    root_id=_root_id(root)
    name=" ".join(str(label or "").split())[:120] or root.name[:120] or "Media"
    connection=_connect()
    try:
        connection.execute(
            """INSERT INTO media_roots(root_id,label,root_path,enabled)
               VALUES (?,?,?,1)
               ON CONFLICT(root_id) DO UPDATE SET label=excluded.label,enabled=1,updated_at=CURRENT_TIMESTAMP""",
            (root_id,name,str(root)),
        )
        connection.commit()
        row=connection.execute("SELECT * FROM media_roots WHERE root_id=?",(root_id,)).fetchone()
    finally:
        connection.close()
    return {"contract":CONTRACT,"root":_public_root(row),"owner_granted":True}




def add_mapped_root(
    path_value:str,
    label:str="",
    *,
    computer_name:str="",
    source_hint:str="",
    source_kind:str="computer_folder",
)->dict[str,Any]:
    if source_kind not in {"computer_folder","network_share","local_folder"}:
        raise MediaServerError("Unsupported mapped media source kind.")
    base=add_root(path_value,label)
    root_id=str(base["root"]["root_id"])
    name=" ".join(str(computer_name or "").split())[:120]
    hint=" ".join(str(source_hint or "").split())[:240]
    connection=_connect()
    try:
        connection.execute(
            """INSERT INTO media_mapped_sources(root_id,source_kind,computer_name,source_hint,connected,last_checked_at)
               VALUES (?,?,?,?,1,CURRENT_TIMESTAMP)
               ON CONFLICT(root_id) DO UPDATE SET
                 source_kind=excluded.source_kind,computer_name=excluded.computer_name,
                 source_hint=excluded.source_hint,connected=1,last_checked_at=CURRENT_TIMESTAMP""",
            (root_id,source_kind,name,hint),
        )
        connection.commit()
        row=connection.execute("SELECT * FROM media_roots WHERE root_id=?",(root_id,)).fetchone()
        mapped=connection.execute("SELECT * FROM media_mapped_sources WHERE root_id=?",(root_id,)).fetchone()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "root":_public_root(row,dict(mapped) if mapped else None),
        "owner_granted":True,
        "mapped_from_computer":source_kind=="computer_folder",
        "source_files_copied":False,
    }


def check_root(root_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM media_roots WHERE root_id=?",(root_id,)).fetchone()
        if not row:
            raise MediaServerError("Media root not found.",404)
        mapped=connection.execute("SELECT * FROM media_mapped_sources WHERE root_id=?",(root_id,)).fetchone()
        path=Path(str(row["root_path"]))
        connected=bool(path.is_dir() and not path.is_symlink())
        connection.execute(
            """INSERT INTO media_mapped_sources(root_id,source_kind,computer_name,source_hint,connected,last_checked_at)
               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(root_id) DO UPDATE SET connected=excluded.connected,last_checked_at=CURRENT_TIMESTAMP""",
            (
                root_id,
                str(mapped["source_kind"]) if mapped else "local_folder",
                str(mapped["computer_name"]) if mapped else "",
                str(mapped["source_hint"]) if mapped else "",
                1 if connected else 0,
            ),
        )
        connection.commit()
        mapped=connection.execute("SELECT * FROM media_mapped_sources WHERE root_id=?",(root_id,)).fetchone()
    finally:
        connection.close()
    return {"contract":CONTRACT,"root":_public_root(row,dict(mapped) if mapped else None)}


def audio_source_records(limit:int=100000)->list[dict[str,Any]]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT mi.media_id,mi.root_id,mi.relative_path,mi.file_name,mi.title,mi.size_bytes,mi.updated_at
               FROM media_items mi
               WHERE mi.media_type='audio'
               ORDER BY mi.root_id,mi.relative_path LIMIT ?""",
            (max(1,min(int(limit),MAX_LIBRARY_FILES)),),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]

def remove_root(root_id:str)->dict[str,Any]:
    _ensure_app()
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM media_roots WHERE root_id=?",(root_id,)).fetchone()
        if not row:
            raise MediaServerError("Media root not found.",404)
        count=int(connection.execute("SELECT COUNT(*) FROM media_items WHERE root_id=?",(root_id,)).fetchone()[0])
        connection.execute("DELETE FROM media_roots WHERE root_id=?",(root_id,))
        connection.commit()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "removed":True,
        "root_id":root_id,
        "indexed_items_removed":count,
        "media_files_deleted":False,
    }


def roots()->dict[str,Any]:
    _ensure_app()
    connection=_connect()
    try:
        rows=connection.execute("SELECT * FROM media_roots ORDER BY label,root_id").fetchall()
        mapped={str(row["root_id"]):dict(row) for row in connection.execute("SELECT * FROM media_mapped_sources").fetchall()}
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "roots":[_public_root(row,mapped.get(str(row["root_id"]))) for row in rows],
        "count":len(rows),
        "mapped_sources":sum(1 for row in mapped.values() if row.get("source_kind") in {"computer_folder","network_share"}),
    }


def _kind(path:Path)->str|None:
    ext=path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return None


def scan(root_id:str="")->dict[str,Any]:
    _ensure_app()
    _require_files_permission()
    connection=_connect()
    try:
        if root_id:
            rows=connection.execute("SELECT * FROM media_roots WHERE root_id=? AND enabled=1",(root_id,)).fetchall()
            if not rows:
                raise MediaServerError("Media root not found.",404)
        else:
            rows=connection.execute("SELECT * FROM media_roots WHERE enabled=1 ORDER BY root_id").fetchall()
        generation=int(connection.execute("SELECT COALESCE(MAX(scan_generation),0)+1 FROM media_items").fetchone()[0])
        scanned=0
        skipped=0
        by_type={"video":0,"audio":0,"image":0}
        touched=[]
        for root_row in rows:
            root=Path(str(root_row["root_path"])).resolve()
            if not root.is_dir() or root.is_symlink():
                continue
            rid=str(root_row["root_id"])
            touched.append(rid)
            for base,dirs,files in os.walk(root,followlinks=False):
                base_path=Path(base)
                dirs[:]=[d for d in dirs if not (base_path/d).is_symlink()]
                for name in files:
                    if scanned>=MAX_LIBRARY_FILES:
                        raise MediaServerError("Media library scan reached the 100,000 file safety limit.",413)
                    path=base_path/name
                    if path.is_symlink():
                        skipped+=1
                        continue
                    kind=_kind(path)
                    if not kind:
                        continue
                    try:
                        resolved=path.resolve(strict=True)
                        if root not in resolved.parents:
                            skipped+=1
                            continue
                        stat=resolved.stat()
                        rel=resolved.relative_to(root).as_posix()
                    except (OSError,RuntimeError,ValueError):
                        skipped+=1
                        continue
                    mime=mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
                    mid=_media_id(rid,rel)
                    connection.execute(
                        """INSERT INTO media_items(
                            media_id,root_id,relative_path,file_name,title,media_type,mime_type,extension,
                            size_bytes,mtime_ns,scan_generation
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(media_id) DO UPDATE SET
                            file_name=excluded.file_name,title=excluded.title,media_type=excluded.media_type,
                            mime_type=excluded.mime_type,extension=excluded.extension,size_bytes=excluded.size_bytes,
                            mtime_ns=excluded.mtime_ns,scan_generation=excluded.scan_generation,updated_at=CURRENT_TIMESTAMP""",
                        (mid,rid,rel,resolved.name,resolved.stem[:300],kind,mime,resolved.suffix.lower(),int(stat.st_size),int(stat.st_mtime_ns),generation),
                    )
                    scanned+=1
                    by_type[kind]+=1
            connection.execute("DELETE FROM media_items WHERE root_id=? AND scan_generation<>?",(rid,generation))
        connection.commit()
        total=int(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0])
    finally:
        connection.close()
    homeserver_app_resources.enforce_sqlite_quota(APP_KEY)
    return {
        "contract":CONTRACT,
        "scanned":scanned,
        "skipped":skipped,
        "library_count":total,
        "types":by_type,
        "roots_scanned":len(touched),
        "transcoding":False,
    }


def _public_item(row:sqlite3.Row)->dict[str,Any]:
    return {
        "media_id":str(row["media_id"]),
        "root_id":str(row["root_id"]),
        "name":str(row["file_name"]),
        "title":str(row["title"]),
        "media_type":str(row["media_type"]),
        "mime_type":str(row["mime_type"]),
        "extension":str(row["extension"]),
        "size_bytes":int(row["size_bytes"]),
        "updated_at":str(row["updated_at"]),
        "stream_url":f"/api/v1/control/homeserver-apps/media-server/stream/{row['media_id']}",
        "absolute_path_exposed":False,
    }


def library(query:str="",media_type:str="",limit:int=100,offset:int=0)->dict[str,Any]:
    _ensure_app()
    q=" ".join(str(query or "").split())[:200]
    kind=str(media_type or "").strip().lower()
    if kind and kind not in {"video","audio","image"}:
        raise MediaServerError("media_type must be video, audio, or image.")
    bounded=max(1,min(int(limit),500))
    skip=max(0,min(int(offset),1_000_000))
    clauses=[]
    params=[]
    if q:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(file_name) LIKE ?)")
        term="%"+q.lower().replace("%","\\%").replace("_","\\_")+"%"
        params.extend([term,term])
    if kind:
        clauses.append("media_type=?")
        params.append(kind)
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    connection=_connect()
    try:
        rows=connection.execute(
            "SELECT * FROM media_items"+where+" ORDER BY title COLLATE NOCASE,media_id LIMIT ? OFFSET ?",
            (*params,bounded,skip),
        ).fetchall()
        total=int(connection.execute("SELECT COUNT(*) FROM media_items"+where,tuple(params)).fetchone()[0])
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "items":[_public_item(row) for row in rows],
        "count":len(rows),
        "total":total,
        "offset":skip,
        "query":q,
        "media_type":kind,
    }


def item(media_id:str)->dict[str,Any]:
    _ensure_app()
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM media_items WHERE media_id=?",(str(media_id),)).fetchone()
        if not row:
            raise MediaServerError("Media item not found.",404)
        playback=connection.execute("SELECT * FROM media_playback WHERE media_id=?",(str(media_id),)).fetchone()
    finally:
        connection.close()
    result=_public_item(row)
    result["playback"]={
        "position_seconds":float(playback["position_seconds"]) if playback else 0.0,
        "duration_seconds":float(playback["duration_seconds"]) if playback else 0.0,
        "completed":bool(playback["completed"]) if playback else False,
        "updated_at":str(playback["updated_at"]) if playback else None,
    }
    return {"contract":CONTRACT,"item":result}


def resolve_stream(media_id:str)->tuple[Path,str,dict[str,Any]]:
    _ensure_app()
    _require_files_permission()
    connection=_connect()
    try:
        row=connection.execute(
            """SELECT mi.*,mr.root_path,mr.enabled FROM media_items mi
               JOIN media_roots mr ON mr.root_id=mi.root_id WHERE mi.media_id=?""",
            (str(media_id),),
        ).fetchone()
    finally:
        connection.close()
    if not row or not bool(row["enabled"]):
        raise MediaServerError("Media item not found.",404)
    root=Path(str(row["root_path"])).resolve()
    rel=PurePosixPath(str(row["relative_path"]))
    if rel.is_absolute() or any(part in {"",".."} for part in rel.parts):
        raise MediaServerError("Indexed media path is invalid.",500)
    target=(root/Path(*rel.parts)).resolve()
    if root not in target.parents or not target.is_file() or target.is_symlink():
        raise MediaServerError("Media file is unavailable.",404)
    stat=target.stat()
    if int(stat.st_size)!=int(row["size_bytes"]) or int(stat.st_mtime_ns)!=int(row["mtime_ns"]):
        raise MediaServerError("Media file changed since the last scan. Rescan the library before playback.",409)
    return target,str(row["mime_type"]),_public_item(row)


def update_playback(media_id:str,position_seconds:float,duration_seconds:float=0.0,completed:bool=False)->dict[str,Any]:
    _ensure_app()
    position=max(0.0,float(position_seconds))
    duration=max(0.0,float(duration_seconds))
    connection=_connect()
    try:
        exists=connection.execute("SELECT 1 FROM media_items WHERE media_id=?",(str(media_id),)).fetchone()
        if not exists:
            raise MediaServerError("Media item not found.",404)
        connection.execute(
            """INSERT INTO media_playback(media_id,position_seconds,duration_seconds,completed,updated_at)
               VALUES (?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(media_id) DO UPDATE SET position_seconds=excluded.position_seconds,
               duration_seconds=excluded.duration_seconds,completed=excluded.completed,updated_at=CURRENT_TIMESTAMP""",
            (str(media_id),position,duration,1 if completed else 0),
        )
        connection.commit()
    finally:
        connection.close()
    return item(media_id)


def _setting(key:str,default:str="")->str:
    connection=_connect()
    try:
        row=connection.execute("SELECT setting_value FROM media_settings WHERE setting_key=?",(key,)).fetchone()
    finally:
        connection.close()
    return str(row["setting_value"]) if row else default


def _set_setting(key:str,value:str)->None:
    connection=_connect()
    try:
        connection.execute(
            """INSERT INTO media_settings(setting_key,setting_value,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=CURRENT_TIMESTAMP""",
            (key,value),
        )
        connection.commit()
    finally:
        connection.close()


def enable_remote_access()->dict[str,Any]:
    _ensure_app()
    token=secrets.token_urlsafe(36)
    _set_setting("remote_access_enabled","1")
    _set_setting("remote_access_token_sha256",hashlib.sha256(token.encode()).hexdigest())
    return {
        "contract":CONTRACT,
        "remote_access_enabled":True,
        "access_key":token,
        "access_key_returned_once":True,
        "public_without_key":False,
    }


def disable_remote_access()->dict[str,Any]:
    _ensure_app()
    _set_setting("remote_access_enabled","0")
    _set_setting("remote_access_token_sha256","")
    return remote_status()


def remote_status()->dict[str,Any]:
    enabled=_setting("remote_access_enabled","0")=="1"
    return {
        "contract":CONTRACT,
        "remote_access_enabled":enabled,
        "access_key_configured":bool(_setting("remote_access_token_sha256","")),
        "public_without_key":False,
        "hosting_supported":True,
    }


def stream_ticket(media_id:str,ttl_seconds:int=300)->dict[str,Any]:
    _ensure_app()
    item(media_id)
    secret=_setting("remote_access_token_sha256","")
    if _setting("remote_access_enabled","0")!="1" or not secret:
        raise MediaServerError("Remote Media Server access is disabled.",403)
    ttl=max(30,min(int(ttl_seconds),900))
    expires=int(time.time())+ttl
    signature=hmac.new(secret.encode(),f"{media_id}|{expires}".encode(),hashlib.sha256).hexdigest()
    return {
        "contract":CONTRACT,
        "media_id":media_id,
        "expires_at":expires,
        "ticket":f"{expires}.{signature}",
        "stream_url":f"/__vp3_media__/stream/{media_id}?ticket={expires}.{signature}",
    }


def authenticate_stream_ticket(media_id:str,ticket:str)->bool:
    secret=_setting("remote_access_token_sha256","")
    if _setting("remote_access_enabled","0")!="1" or not secret:
        return False
    raw=str(ticket or "")
    if "." not in raw:
        return False
    exp_raw,signature=raw.split(".",1)
    try:
        expires=int(exp_raw)
    except ValueError:
        return False
    if expires<int(time.time()) or expires>int(time.time())+900:
        return False
    expected=hmac.new(secret.encode(),f"{media_id}|{expires}".encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,signature)


def authenticate_remote(token:str)->bool:
    if _setting("remote_access_enabled","0")!="1":
        return False
    expected=_setting("remote_access_token_sha256","")
    candidate=hashlib.sha256(str(token or "").encode()).hexdigest()
    return bool(expected) and secrets.compare_digest(expected,candidate)


def status()->dict[str,Any]:
    app=_ensure_app()
    connection=_connect()
    try:
        counts={row["media_type"]:int(row["c"]) for row in connection.execute("SELECT media_type,COUNT(*) c FROM media_items GROUP BY media_type").fetchall()}
        total=int(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0])
        roots_count=int(connection.execute("SELECT COUNT(*) FROM media_roots WHERE enabled=1").fetchone()[0])
        recent=[dict(row) for row in connection.execute(
            """SELECT mi.media_id,mi.title,mp.position_seconds,mp.duration_seconds,mp.completed,mp.updated_at
               FROM media_playback mp JOIN media_items mi ON mi.media_id=mp.media_id
               ORDER BY mp.updated_at DESC LIMIT 12"""
        ).fetchall()]
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "app_key":APP_KEY,
        "installed_version":app.get("installed_version"),
        "lifecycle_state":app.get("lifecycle_state"),
        "library_count":total,
        "roots":roots_count,
        "types":{"video":counts.get("video",0),"audio":counts.get("audio",0),"image":counts.get("image",0)},
        "recently_played":recent,
        "remote":remote_status(),
        "transcoding":False,
        "source_media_owned_by_app":False,
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "app_key":APP_KEY,
        "owner_granted_media_roots":True,
        "mapped_computer_folders":True,
        "network_share_roots":True,
        "mapped_source_health":True,
        "opaque_media_ids":True,
        "absolute_paths_exposed":False,
        "symlinks":False,
        "incremental_index":True,
        "video":True,
        "audio":True,
        "images":True,
        "byte_range_streaming":"file_response",
        "playback_resume":True,
        "recently_played":True,
        "remote_access_key":True,
        "short_lived_stream_tickets":True,
        "public_without_key":False,
        "hosting_subdomain":True,
        "custom_domain":True,
        "transcoding":False,
        "dlna":False,
        "chromecast":False,
        "source_media_deleted_on_uninstall":False,
    }

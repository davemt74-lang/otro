from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import homeserver_app_resources, homeserver_app_runtime, homeserver_app_security, homeserver_apps

APP_KEY="vp3.download-manager"
CONTRACT="vp3.download-manager.v1"
MAX_DOWNLOAD_BYTES=100*1024*1024*1024
CHUNK_BYTES=256*1024
_FILENAME_RE=re.compile(r"[^A-Za-z0-9._ ()\[\]-]+")
_WORKER_LOCK=threading.RLock()
_WORKER:threading.Thread|None=None
_STOP=threading.Event()
_RECOVERED=False


class DownloadManagerError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _event(topic:str,payload:dict[str,Any])->None:
    try:
        homeserver_app_runtime.publish_event(APP_KEY,topic,payload,source="download-manager")
    except Exception:
        pass


def _ensure_app()->dict[str,Any]:
    try:
        app=homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise DownloadManagerError("VP3 Download Manager is not installed.",404) from exc
    if not app.get("installed_version"):
        raise DownloadManagerError("VP3 Download Manager is not installed.",409)
    return app


def _connect()->sqlite3.Connection:
    _ensure_app()
    path=homeserver_app_resources.sqlite_path(APP_KEY,"download-manager.db")
    connection=sqlite3.connect(path,timeout=20,check_same_thread=False)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS download_destinations(
            destination_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            root_path TEXT NOT NULL,
            destination_kind TEXT NOT NULL DEFAULT 'app_storage',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS download_jobs(
            download_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            display_url TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            requested_filename TEXT NOT NULL DEFAULT '',
            final_filename TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            bytes_total INTEGER,
            bytes_downloaded INTEGER NOT NULL DEFAULT 0,
            etag TEXT NOT NULL DEFAULT '',
            last_modified TEXT NOT NULL DEFAULT '',
            checksum_algorithm TEXT NOT NULL DEFAULT '',
            checksum_expected TEXT NOT NULL DEFAULT '',
            checksum_actual TEXT NOT NULL DEFAULT '',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            error TEXT NOT NULL DEFAULT '',
            speed_bps REAL NOT NULL DEFAULT 0,
            scheduled_at INTEGER,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(destination_id) REFERENCES download_destinations(destination_id)
        );
        CREATE INDEX IF NOT EXISTS idx_download_jobs_state
          ON download_jobs(status,priority DESC,scheduled_at,created_at);
        CREATE TABLE IF NOT EXISTS download_settings(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            max_concurrent INTEGER NOT NULL DEFAULT 2,
            bandwidth_limit_bps INTEGER NOT NULL DEFAULT 0,
            max_file_bytes INTEGER NOT NULL DEFAULT 10737418240,
            auto_retry INTEGER NOT NULL DEFAULT 1 CHECK(auto_retry IN (0,1)),
            blocked_extensions_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO download_settings(singleton) VALUES (1);
        """
    )
    columns={str(row["name"]) for row in connection.execute("PRAGMA table_info(download_settings)").fetchall()}
    if "blocked_extensions_json" not in columns:
        connection.execute("ALTER TABLE download_settings ADD COLUMN blocked_extensions_json TEXT NOT NULL DEFAULT '[]'")
        connection.commit()
    default_root=homeserver_app_resources.files_root(APP_KEY).resolve()/"downloads"
    default_root.mkdir(parents=True,exist_ok=True)
    connection.execute(
        """INSERT OR IGNORE INTO download_destinations(
             destination_id,label,root_path,destination_kind,enabled
           ) VALUES ('app-storage','App Downloads',?,'app_storage',1)""",
        (str(default_root),),
    )
    connection.commit()
    return connection


def _settings(connection:sqlite3.Connection|None=None)->dict[str,Any]:
    own=connection is None
    connection=connection or _connect()
    try:
        row=connection.execute("SELECT * FROM download_settings WHERE singleton=1").fetchone()
        return {
            "max_concurrent":int(row["max_concurrent"]),
            "bandwidth_limit_bps":int(row["bandwidth_limit_bps"]),
            "max_file_bytes":int(row["max_file_bytes"]),
            "auto_retry":bool(row["auto_retry"]),
            "blocked_extensions":list(json.loads(row["blocked_extensions_json"] or "[]")),
        }
    finally:
        if own:
            connection.close()


def settings()->dict[str,Any]:
    return {"contract":CONTRACT,"settings":_settings()}


def update_settings(values:dict[str,Any])->dict[str,Any]:
    allowed={"max_concurrent","bandwidth_limit_bps","max_file_bytes","auto_retry","blocked_extensions"}
    unknown=set(values)-allowed
    if unknown:
        raise DownloadManagerError(f"Unknown Download Manager setting: {sorted(unknown)[0]}")
    current=_settings()
    if "max_concurrent" in values:
        n=int(values["max_concurrent"])
        if n<1 or n>8:
            raise DownloadManagerError("max_concurrent must be between 1 and 8.")
        current["max_concurrent"]=n
    if "bandwidth_limit_bps" in values:
        n=int(values["bandwidth_limit_bps"])
        if n<0 or n>1024*1024*1024:
            raise DownloadManagerError("bandwidth_limit_bps is out of range.")
        current["bandwidth_limit_bps"]=n
    if "max_file_bytes" in values:
        n=int(values["max_file_bytes"])
        if n<1024*1024 or n>MAX_DOWNLOAD_BYTES:
            raise DownloadManagerError("max_file_bytes is out of range.")
        current["max_file_bytes"]=n
    if "auto_retry" in values:
        current["auto_retry"]=bool(values["auto_retry"])
    if "blocked_extensions" in values:
        raw=values["blocked_extensions"]
        if not isinstance(raw,list) or len(raw)>100:
            raise DownloadManagerError("blocked_extensions must be a list of at most 100 extensions.")
        cleaned=[]
        for value in raw:
            ext=str(value or "").strip().lower()
            if ext and not ext.startswith("."): ext="."+ext
            if ext and (len(ext)>20 or not re.fullmatch(r"\.[a-z0-9][a-z0-9._-]*",ext)):
                raise DownloadManagerError("A blocked file extension is invalid.")
            if ext and ext not in cleaned: cleaned.append(ext)
        current["blocked_extensions"]=cleaned
    connection=_connect()
    try:
        connection.execute(
            """UPDATE download_settings SET max_concurrent=?,bandwidth_limit_bps=?,max_file_bytes=?,
               auto_retry=?,blocked_extensions_json=?,updated_at=CURRENT_TIMESTAMP WHERE singleton=1""",
            (
                current["max_concurrent"],current["bandwidth_limit_bps"],current["max_file_bytes"],
                1 if current["auto_retry"] else 0,
                json.dumps(current["blocked_extensions"],separators=(",",":")),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return settings()


def _safe_filename(value:str,fallback:str="download.bin")->str:
    raw=Path(str(value or "")).name.strip()
    raw=_FILENAME_RE.sub("_",raw).strip(" .")
    return (raw or fallback)[:240]


def _destination_public(row:sqlite3.Row|dict[str,Any])->dict[str,Any]:
    return {
        "destination_id":str(row["destination_id"]),
        "label":str(row["label"]),
        "destination_kind":str(row["destination_kind"]),
        "enabled":bool(row["enabled"]),
        "absolute_path_exposed":False,
    }


def destinations()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute("SELECT * FROM download_destinations ORDER BY destination_kind,label").fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"destinations":[_destination_public(row) for row in rows],"count":len(rows)}


def add_destination(path_value:str,label:str="",destination_kind:str="mapped_folder")->dict[str,Any]:
    homeserver_app_security.require_permission(APP_KEY,"files.write")
    root=Path(str(path_value or "")).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise DownloadManagerError("Download destination must be an existing non-symlink folder.")
    kind=str(destination_kind or "mapped_folder")
    if kind not in {"mapped_folder","network_share","local_folder"}:
        raise DownloadManagerError("Unsupported download destination kind.")
    name=" ".join(str(label or root.name or "Download Destination").split())[:120]
    destination_id="dest_"+uuid.uuid4().hex
    connection=_connect()
    try:
        connection.execute(
            "INSERT INTO download_destinations(destination_id,label,root_path,destination_kind) VALUES (?,?,?,?)",
            (destination_id,name,str(root),kind),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "destination":_destination_public({
            "destination_id":destination_id,"label":name,"destination_kind":kind,"enabled":1,
        }),
        "owner_granted":True,
    }


def remove_destination(destination_id:str)->dict[str,Any]:
    if destination_id=="app-storage":
        raise DownloadManagerError("The app-owned destination cannot be removed.",409)
    connection=_connect()
    try:
        active=int(connection.execute(
            "SELECT COUNT(*) FROM download_jobs WHERE destination_id=? AND status IN ('queued','downloading','paused')",
            (destination_id,),
        ).fetchone()[0])
        if active:
            raise DownloadManagerError("Destination is in use by active downloads.",409)
        cur=connection.execute("DELETE FROM download_destinations WHERE destination_id=?",(destination_id,))
        if cur.rowcount<1:
            raise DownloadManagerError("Download destination not found.",404)
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"removed":True,"destination_id":destination_id,"files_deleted":False}


def _destination_path(destination_id:str)->tuple[Path,str]:
    connection=_connect()
    try:
        row=connection.execute(
            "SELECT * FROM download_destinations WHERE destination_id=? AND enabled=1",(destination_id,)
        ).fetchone()
    finally:
        connection.close()
    if not row:
        raise DownloadManagerError("Download destination is unavailable.",409)
    root=Path(str(row["root_path"])).resolve()
    if not root.is_dir() or root.is_symlink():
        raise DownloadManagerError("Download destination is disconnected.",409)
    kind=str(row["destination_kind"])
    if kind!="app_storage":
        homeserver_app_security.require_permission(APP_KEY,"files.write")
    return root,kind


def _validate_url(url:str)->urllib.parse.SplitResult:
    raw=str(url or "").strip()
    if not raw or len(raw)>4096:
        raise DownloadManagerError("Download URL is invalid.")
    parsed=urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() not in {"http","https"}:
        raise DownloadManagerError("Only HTTP and HTTPS downloads are supported.")
    if parsed.username or parsed.password:
        raise DownloadManagerError("Credentials must not be embedded in download URLs.")
    if not parsed.hostname:
        raise DownloadManagerError("Download URL requires a host.")
    host=parsed.hostname.rstrip(".")
    try:
        infos=socket.getaddrinfo(host,parsed.port or (443 if parsed.scheme=="https" else 80),type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DownloadManagerError("Download host could not be resolved.",422) from exc
    addresses={info[4][0].split("%",1)[0] for info in infos}
    if not addresses:
        raise DownloadManagerError("Download host could not be resolved.",422)
    for value in addresses:
        try:
            ip=ipaddress.ip_address(value)
        except ValueError as exc:
            raise DownloadManagerError("Download host resolved to an invalid address.",422) from exc
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise DownloadManagerError("Downloads from local or private network addresses are blocked.",403)
    return parsed


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections=5
    max_repeats=2
    def redirect_request(self,req,fp,code,msg,headers,newurl):  # noqa: ANN001
        _validate_url(newurl)
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected is not None:
            old_host=(urllib.parse.urlsplit(req.full_url).hostname or "").lower()
            new_host=(urllib.parse.urlsplit(newurl).hostname or "").lower()
            if old_host!=new_host:
                redirected.remove_header("Authorization")
        return redirected


def _open_url(url:str,headers:dict[str,str],timeout:int=30):
    homeserver_app_security.require_permission(APP_KEY,"network.external")
    _validate_url(url)
    opener=urllib.request.build_opener(_SafeRedirect())
    request=urllib.request.Request(url,headers=headers,method="GET")
    return opener.open(request,timeout=timeout)


def _content_filename(headers:Any,url:str,requested:str)->str:
    if requested:
        return _safe_filename(requested)
    disposition=str(headers.get("Content-Disposition") or "")
    match=re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^;"\']+)',disposition,re.I)
    if match:
        try:
            return _safe_filename(urllib.parse.unquote(match.group(1)))
        except Exception:
            pass
    name=Path(urllib.parse.urlsplit(url).path).name
    return _safe_filename(name or "download.bin")


def _unique_final(root:Path,filename:str)->Path:
    candidate=(root/filename).resolve()
    if candidate.parent!=root:
        raise DownloadManagerError("Download filename escaped its destination.")
    if not candidate.exists():
        return candidate
    stem=candidate.stem
    suffix=candidate.suffix
    for i in range(1,10000):
        other=(root/f"{stem} ({i}){suffix}").resolve()
        if other.parent==root and not other.exists():
            return other
    raise DownloadManagerError("Unable to choose a unique download filename.",409)


def _public_job(row:sqlite3.Row|dict[str,Any])->dict[str,Any]:
    item=dict(row)
    item.pop("url",None)
    item["source_url_exposed"]=False
    item["display_url"]=str(item.get("display_url") or "")
    item["bytes_total"]=None if item.get("bytes_total") is None else int(item["bytes_total"])
    item["bytes_downloaded"]=int(item.get("bytes_downloaded") or 0)
    item["priority"]=int(item.get("priority") or 0)
    item["retry_count"]=int(item.get("retry_count") or 0)
    item["max_retries"]=int(item.get("max_retries") or 0)
    item["speed_bps"]=float(item.get("speed_bps") or 0)
    item["progress"]=(
        min(1.0,item["bytes_downloaded"]/item["bytes_total"])
        if item["bytes_total"] and item["bytes_total"]>0 else None
    )
    item["eta_seconds"]=(
        max(0,int((item["bytes_total"]-item["bytes_downloaded"])/item["speed_bps"]))
        if item["bytes_total"] and item["speed_bps"]>0 else None
    )
    return item


def list_downloads(status:str="",limit:int=200)->dict[str,Any]:
    connection=_connect()
    try:
        if status:
            rows=connection.execute(
                "SELECT * FROM download_jobs WHERE status=? ORDER BY priority DESC,created_at DESC LIMIT ?",
                (status,max(1,min(int(limit),1000))),
            ).fetchall()
        else:
            rows=connection.execute(
                "SELECT * FROM download_jobs ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END,priority DESC,created_at DESC LIMIT ?",
                (max(1,min(int(limit),1000)),),
            ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"downloads":[_public_job(row) for row in rows],"count":len(rows)}


def get_download(download_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
    finally:
        connection.close()
    if not row:
        raise DownloadManagerError("Download not found.",404)
    return {"contract":CONTRACT,"download":_public_job(row)}


def enqueue(
    url:str,
    *,
    destination_id:str="app-storage",
    filename:str="",
    priority:int=0,
    checksum_algorithm:str="",
    checksum_expected:str="",
    max_retries:int=3,
    scheduled_at:int|None=None,
)->dict[str,Any]:
    homeserver_app_security.require_permission(APP_KEY,"network.external")
    parsed=_validate_url(url)
    _destination_path(destination_id)
    prio=max(-100,min(100,int(priority)))
    retries=max(0,min(10,int(max_retries)))
    algorithm=str(checksum_algorithm or "").strip().lower()
    expected=str(checksum_expected or "").strip().lower()
    if algorithm not in {"","sha256","sha512"}:
        raise DownloadManagerError("Checksum algorithm must be sha256 or sha512.")
    if bool(algorithm)!=bool(expected):
        raise DownloadManagerError("Checksum algorithm and expected digest must be provided together.")
    if expected and not re.fullmatch(r"[0-9a-f]+",expected):
        raise DownloadManagerError("Expected checksum must be hexadecimal.")
    if algorithm=="sha256" and len(expected)!=64:
        raise DownloadManagerError("SHA-256 checksum must be 64 hexadecimal characters.")
    if algorithm=="sha512" and len(expected)!=128:
        raise DownloadManagerError("SHA-512 checksum must be 128 hexadecimal characters.")
    when=None if scheduled_at is None else int(scheduled_at)
    display=f"{parsed.scheme.lower()}://{parsed.hostname}{parsed.path or '/'}"
    download_id="dl_"+uuid.uuid4().hex
    connection=_connect()
    try:
        connection.execute(
            """INSERT INTO download_jobs(
                download_id,url,display_url,destination_id,requested_filename,status,priority,
                checksum_algorithm,checksum_expected,max_retries,scheduled_at
            ) VALUES (?,?,?,?,?,'queued',?,?,?,?,?)""",
            (download_id,url,display,destination_id,_safe_filename(filename,"") if filename else "",prio,algorithm,expected,retries,when),
        )
        connection.commit()
    finally:
        connection.close()
    _event("downloads.queued",{"download_id":download_id,"destination_id":destination_id,"priority":prio})
    _ensure_worker()
    return get_download(download_id)


def pause(download_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT status FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
        if not row:
            raise DownloadManagerError("Download not found.",404)
        if str(row["status"]) not in {"queued","downloading"}:
            raise DownloadManagerError("Only queued or active downloads can be paused.",409)
        connection.execute(
            "UPDATE download_jobs SET status='paused',updated_at=CURRENT_TIMESTAMP WHERE download_id=?",(download_id,)
        )
        connection.commit()
    finally:
        connection.close()
    _event("downloads.paused",{"download_id":download_id})
    return get_download(download_id)


def resume(download_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT status FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
        if not row:
            raise DownloadManagerError("Download not found.",404)
        if str(row["status"]) not in {"paused","failed","cancelled"}:
            raise DownloadManagerError("This download cannot be resumed.",409)
        connection.execute(
            """UPDATE download_jobs SET status='queued',error='',completed_at=NULL,
               updated_at=CURRENT_TIMESTAMP WHERE download_id=?""",(download_id,)
        )
        connection.commit()
    finally:
        connection.close()
    _event("downloads.resumed",{"download_id":download_id})
    _ensure_worker()
    return get_download(download_id)


def cancel(download_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT status FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
        if not row:
            raise DownloadManagerError("Download not found.",404)
        if str(row["status"]) in {"completed","cancelled"}:
            return get_download(download_id)
        connection.execute(
            "UPDATE download_jobs SET status='cancelled',completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE download_id=?",
            (download_id,),
        )
        connection.commit()
    finally:
        connection.close()
    _event("downloads.cancelled",{"download_id":download_id})
    return get_download(download_id)


def retry(download_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT status FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
        if not row:
            raise DownloadManagerError("Download not found.",404)
        if str(row["status"]) not in {"failed","cancelled"}:
            raise DownloadManagerError("Only failed or cancelled downloads can be retried.",409)
        connection.execute(
            """UPDATE download_jobs SET status='queued',error='',retry_count=0,completed_at=NULL,
               updated_at=CURRENT_TIMESTAMP WHERE download_id=?""",(download_id,)
        )
        connection.commit()
    finally:
        connection.close()
    _event("downloads.retry",{"download_id":download_id})
    _ensure_worker()
    return get_download(download_id)


def set_priority(download_id:str,priority:int)->dict[str,Any]:
    value=max(-100,min(100,int(priority)))
    connection=_connect()
    try:
        cur=connection.execute(
            "UPDATE download_jobs SET priority=?,updated_at=CURRENT_TIMESTAMP WHERE download_id=?",
            (value,download_id),
        )
        if cur.rowcount<1:
            raise DownloadManagerError("Download not found.",404)
        connection.commit()
    finally:
        connection.close()
    return get_download(download_id)


def clear_history()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            "SELECT download_id,destination_id,status FROM download_jobs WHERE status IN ('completed','failed','cancelled')"
        ).fetchall()
        for row in rows:
            try:
                root,_kind=_destination_path(str(row["destination_id"]))
                _temp_path(root,str(row["download_id"])).unlink(missing_ok=True)
            except Exception:
                pass
        cursor=connection.execute(
            "DELETE FROM download_jobs WHERE status IN ('completed','failed','cancelled')"
        )
        removed=int(cursor.rowcount)
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"removed":removed,"files_deleted":False,"temporary_files_removed":True}


def _temp_path(root:Path,download_id:str)->Path:
    temp=(root/f".vp3-{download_id}.part").resolve()
    if temp.parent!=root:
        raise DownloadManagerError("Temporary download path escaped its destination.")
    return temp


def _status(download_id:str)->str:
    connection=_connect()
    try:
        row=connection.execute("SELECT status FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
    finally:
        connection.close()
    return str(row["status"]) if row else "cancelled"


def _update_progress(download_id:str,downloaded:int,total:int|None,speed:float,etag:str="",last_modified:str="")->None:
    connection=_connect()
    try:
        connection.execute(
            """UPDATE download_jobs SET bytes_downloaded=?,bytes_total=?,speed_bps=?,etag=?,last_modified=?,
               updated_at=CURRENT_TIMESTAMP WHERE download_id=?""",
            (downloaded,total,speed,etag,last_modified,download_id),
        )
        connection.commit()
    finally:
        connection.close()


def _open_for_job(row:dict[str,Any],resume_at:int):
    headers={"User-Agent":"VP3-HomeServer-DownloadManager/1.0","Accept":"*/*"}
    secret=homeserver_app_security.get_secret(APP_KEY,"AUTHORIZATION_HEADER")
    app=homeserver_apps.get(APP_KEY)
    configured_host=str(((app.get("metadata") or {}).get("control_settings") or {}).get("authorization_host") or "").strip().lower().rstrip(".")
    request_host=(urllib.parse.urlsplit(str(row["url"])).hostname or "").lower().rstrip(".")
    if secret and configured_host and request_host==configured_host:
        headers["Authorization"]=secret
    if resume_at>0:
        headers["Range"]=f"bytes={resume_at}-"
        if row.get("etag"):
            headers["If-Range"]=str(row["etag"])
        elif row.get("last_modified"):
            headers["If-Range"]=str(row["last_modified"])
    return _open_url(str(row["url"]),headers,timeout=30)


def _process(download_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM download_jobs WHERE download_id=?",(download_id,)).fetchone()
        if not row:
            raise DownloadManagerError("Download not found.",404)
        item=dict(row)
        if item["status"]!="queued":
            return get_download(download_id)
        claimed=connection.execute(
            """UPDATE download_jobs SET status='downloading',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),
               error='',updated_at=CURRENT_TIMESTAMP WHERE download_id=? AND status='queued'""",(download_id,)
        )
        if claimed.rowcount!=1:
            connection.rollback()
            return get_download(download_id)
        connection.commit()
    finally:
        connection.close()

    root,kind=_destination_path(str(item["destination_id"]))
    temp=_temp_path(root,download_id)
    resume_at=temp.stat().st_size if temp.is_file() and not temp.is_symlink() else 0
    started=time.monotonic()
    settings_value=_settings()
    max_bytes=min(int(settings_value["max_file_bytes"]),MAX_DOWNLOAD_BYTES)
    try:
        response=_open_for_job(item,resume_at)
        code=int(getattr(response,"status",response.getcode()))
        if resume_at>0 and code!=206:
            resume_at=0
            temp.unlink(missing_ok=True)
        content_length=response.headers.get("Content-Length")
        remaining=int(content_length) if content_length and str(content_length).isdigit() else None
        total=(resume_at+remaining) if remaining is not None else None
        if total is not None and total>max_bytes:
            raise DownloadManagerError("Download exceeds the configured maximum file size.",413)
        filename=_content_filename(response.headers,str(item["url"]),str(item["requested_filename"]))
        if Path(filename).suffix.lower() in set(settings_value.get("blocked_extensions") or []):
            raise DownloadManagerError("This file type is blocked by Download Manager settings.",403)
        mode="ab" if resume_at>0 and code==206 else "wb"
        downloaded=resume_at if mode=="ab" else 0
        digest=hashlib.new(str(item["checksum_algorithm"])) if item.get("checksum_algorithm") else None
        if digest and downloaded:
            with temp.open("rb") as existing:
                while True:
                    block=existing.read(CHUNK_BYTES)
                    if not block: break
                    digest.update(block)
        limit_bps=int(settings_value["bandwidth_limit_bps"])
        etag=str(response.headers.get("ETag") or "")
        modified=str(response.headers.get("Last-Modified") or "")
        with temp.open(mode) as handle:
            while True:
                state=_status(download_id)
                if state=="paused":
                    _update_progress(download_id,downloaded,total,0,etag,modified)
                    return get_download(download_id)
                if state=="cancelled":
                    _update_progress(download_id,downloaded,total,0,etag,modified)
                    return get_download(download_id)
                chunk=response.read(CHUNK_BYTES)
                if not chunk:
                    break
                downloaded+=len(chunk)
                if downloaded>max_bytes:
                    raise DownloadManagerError("Download exceeded the configured maximum file size.",413)
                if kind=="app_storage":
                    resource=homeserver_app_resources.resource_status(APP_KEY)
                    current_part=temp.stat().st_size if temp.exists() else 0
                    projected=resource["storage_used_bytes"]-current_part+downloaded
                    if projected>resource["storage_limit_bytes"]:
                        raise DownloadManagerError("App storage quota would be exceeded.",413)
                handle.write(chunk)
                if digest: digest.update(chunk)
                elapsed=max(0.001,time.monotonic()-started)
                speed=max(0.0,(downloaded-resume_at)/elapsed)
                _update_progress(download_id,downloaded,total,speed,etag,modified)
                if limit_bps>0:
                    expected=(downloaded-resume_at)/limit_bps
                    delay=expected-elapsed
                    if delay>0:
                        time.sleep(min(delay,1.0))
            handle.flush()
            os.fsync(handle.fileno())
        if total is not None and downloaded!=total:
            raise DownloadManagerError("Download ended before the expected content length was received.",502)
        actual=digest.hexdigest().lower() if digest else ""
        if item.get("checksum_expected") and actual!=str(item["checksum_expected"]).lower():
            temp.unlink(missing_ok=True)
            _update_progress(download_id,0,total,0,etag,modified)
            raise DownloadManagerError("Downloaded file checksum did not match the expected digest.",422)
        final=_unique_final(root,filename)
        os.replace(temp,final)
        connection=_connect()
        try:
            connection.execute(
                """UPDATE download_jobs SET status='completed',final_filename=?,bytes_downloaded=?,
                   bytes_total=COALESCE(bytes_total,?),checksum_actual=?,speed_bps=0,error='',
                   completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE download_id=?""",
                (final.name,downloaded,downloaded,actual,download_id),
            )
            connection.commit()
        finally:
            connection.close()
        result=get_download(download_id)
        _event("downloads.completed",{"download_id":download_id,"filename":result["download"].get("final_filename",""),"bytes":downloaded})
        return result
    except Exception as exc:
        connection=_connect()
        try:
            current=connection.execute(
                "SELECT status,retry_count,max_retries FROM download_jobs WHERE download_id=?",(download_id,)
            ).fetchone()
            if current and current["status"] not in {"paused","cancelled"}:
                retries=int(current["retry_count"])+1
                auto=bool(_settings(connection)["auto_retry"])
                next_state="queued" if auto and retries<=int(current["max_retries"]) else "failed"
                retry_at=int(time.time())+min(300,5*(2**max(0,retries-1))) if next_state=="queued" else None
                connection.execute(
                    """UPDATE download_jobs SET status=?,retry_count=?,error=?,speed_bps=0,
                       scheduled_at=CASE WHEN ?='queued' THEN ? ELSE scheduled_at END,
                       completed_at=CASE WHEN ?='failed' THEN CURRENT_TIMESTAMP ELSE NULL END,
                       updated_at=CURRENT_TIMESTAMP WHERE download_id=?""",
                    (next_state,retries,str(exc)[:1000],next_state,retry_at,next_state,download_id),
                )
                connection.commit()
        finally:
            connection.close()
        result=get_download(download_id)
        if result["download"]["status"]=="failed":
            _event("downloads.failed",{"download_id":download_id,"error":result["download"].get("error","")})
        return result


def process_next()->dict[str,Any]|None:
    now=int(time.time())
    connection=_connect()
    try:
        row=connection.execute(
            """SELECT download_id FROM download_jobs
               WHERE status='queued' AND (scheduled_at IS NULL OR scheduled_at<=?)
               ORDER BY priority DESC,created_at LIMIT 1""",(now,)
        ).fetchone()
    finally:
        connection.close()
    return _process(str(row["download_id"])) if row else None


def _worker_loop()->None:
    while not _STOP.wait(0.5):
        try:
            settings_value=_settings()
            connection=_connect()
            try:
                active=int(connection.execute("SELECT COUNT(*) FROM download_jobs WHERE status='downloading'").fetchone()[0])
                rows=connection.execute(
                    """SELECT download_id FROM download_jobs
                       WHERE status='queued' AND (scheduled_at IS NULL OR scheduled_at<=?)
                       ORDER BY priority DESC,created_at LIMIT ?""",
                    (int(time.time()),max(0,int(settings_value["max_concurrent"])-active)),
                ).fetchall()
            finally:
                connection.close()
            for row in rows:
                threading.Thread(target=_process,args=(str(row["download_id"]),),daemon=True).start()
        except Exception:
            continue


def recover_interrupted()->dict[str,Any]:
    connection=_connect()
    try:
        cursor=connection.execute(
            """UPDATE download_jobs SET status='queued',speed_bps=0,
               error='Recovered after HomeServer interruption.',updated_at=CURRENT_TIMESTAMP
               WHERE status='downloading'"""
        )
        recovered=int(cursor.rowcount)
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"recovered":recovered}


def _ensure_worker()->None:
    global _WORKER,_RECOVERED
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        if not _RECOVERED:
            recover_interrupted()
            _RECOVERED=True
        _STOP.clear()
        _WORKER=threading.Thread(target=_worker_loop,name="vp3-download-manager",daemon=True)
        _WORKER.start()


def stop_worker()->None:
    global _WORKER
    _STOP.set()
    worker=_WORKER
    if worker and worker.is_alive():
        worker.join(timeout=2)
    _WORKER=None


def status()->dict[str,Any]:
    _ensure_worker()
    connection=_connect()
    try:
        counts={str(row["status"]):int(row["count"]) for row in connection.execute(
            "SELECT status,COUNT(*) count FROM download_jobs GROUP BY status"
        ).fetchall()}
        row=connection.execute(
            """SELECT COALESCE(SUM(bytes_downloaded),0) downloaded,
                      COALESCE(SUM(CASE WHEN status='completed' THEN bytes_downloaded ELSE 0 END),0) completed_bytes
               FROM download_jobs"""
        ).fetchone()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "app_key":APP_KEY,
        "counts":counts,
        "active":counts.get("downloading",0),
        "queued":counts.get("queued",0),
        "paused":counts.get("paused",0),
        "failed":counts.get("failed",0),
        "completed":counts.get("completed",0),
        "bytes_downloaded":int(row["downloaded"]),
        "completed_bytes":int(row["completed_bytes"]),
        "settings":_settings(),
        "destinations":destinations()["count"],
        "homeserver_execution_authority":True,
    }


def brain_context(limit:int=8)->dict[str,Any]:
    state=status()
    recent=list_downloads(limit=max(1,min(int(limit),20)))["downloads"]
    return {
        "contract":"vp3.download-manager.brain-context.v1",
        "summary":{
            "active":state["active"],"queued":state["queued"],"paused":state["paused"],
            "failed":state["failed"],"completed":state["completed"],
        },
        "attention":[
            {"download_id":row["download_id"],"status":row["status"],"error":row.get("error","")}
            for row in recent if row["status"] in {"failed","paused"}
        ][:8],
        "recent":[
            {k:row.get(k) for k in ("download_id","display_url","status","progress","eta_seconds","final_filename")}
            for row in recent[:8]
        ],
        "source_urls_exposed":False,
        "filesystem_paths_exposed":False,
    }


def invoke(action:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    key=str(action or "").strip()
    if key=="downloads.status": return status()
    if key=="downloads.list": return list_downloads(str(args.get("status") or ""),int(args.get("limit",200)))
    if key=="downloads.get": return get_download(str(args.get("download_id") or ""))
    if key=="downloads.enqueue": return enqueue(
        str(args.get("url") or ""),
        destination_id=str(args.get("destination_id") or "app-storage"),
        filename=str(args.get("filename") or ""),
        priority=int(args.get("priority",0)),
        checksum_algorithm=str(args.get("checksum_algorithm") or ""),
        checksum_expected=str(args.get("checksum_expected") or ""),
        max_retries=int(args.get("max_retries",3)),
        scheduled_at=args.get("scheduled_at"),
    )
    if key=="downloads.pause": return pause(str(args.get("download_id") or ""))
    if key=="downloads.resume": return resume(str(args.get("download_id") or ""))
    if key=="downloads.cancel": return cancel(str(args.get("download_id") or ""))
    if key=="downloads.retry": return retry(str(args.get("download_id") or ""))
    if key=="downloads.priority.set": return set_priority(str(args.get("download_id") or ""),int(args.get("priority",0)))
    if key=="downloads.history.clear": return clear_history()
    if key=="downloads.destinations": return destinations()
    if key=="downloads.destination.add": return add_destination(
        str(args.get("path") or ""),str(args.get("label") or ""),str(args.get("destination_kind") or "mapped_folder")
    )
    if key=="downloads.destination.remove": return remove_destination(str(args.get("destination_id") or ""))
    if key=="downloads.settings": return settings()
    if key=="downloads.settings.update": return update_settings(dict(args.get("values") or {}))
    if key=="downloads.brain-context": return brain_context(int(args.get("limit",8)))
    raise DownloadManagerError("Unsupported Download Manager action.",404)




def enable_remote()->dict[str,Any]:
    key=secrets.token_urlsafe(32)
    homeserver_app_security.set_secret(APP_KEY,"REMOTE_ACCESS_KEY",key)
    return {
        "contract":CONTRACT,
        "remote_enabled":True,
        "access_key":key,
        "access_key_returned_once":True,
    }


def disable_remote()->dict[str,Any]:
    homeserver_app_security.remove_secret(APP_KEY,"REMOTE_ACCESS_KEY")
    return {"contract":CONTRACT,"remote_enabled":False}


def remote_status()->dict[str,Any]:
    configured="REMOTE_ACCESS_KEY" in set(homeserver_app_security.secret_status(APP_KEY).get("configured_keys") or [])
    return {"contract":CONTRACT,"remote_enabled":configured,"access_key_exposed":False}


def authenticate_remote(value:str)->bool:
    expected=homeserver_app_security.get_secret(APP_KEY,"REMOTE_ACCESS_KEY")
    return bool(expected and secrets.compare_digest(str(value or ""),expected))


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "http_https_only":True,
        "private_network_downloads_blocked":True,
        "resume_range_requests":True,
        "atomic_finalization":True,
        "checksum_sha256_sha512":True,
        "pause_resume_cancel_retry":True,
        "restart_recovery":True,
        "priority_queue":True,
        "scheduled_downloads":True,
        "bandwidth_limit":True,
        "concurrency_limit":True,
        "retry_backoff":True,
        "configurable_file_type_blocklist":True,
        "scoped_authentication_host":True,
        "owner_granted_destinations":True,
        "app_owned_default_destination":True,
        "authenticated_downloads_via_secret":True,
        "private_hosted_access_key":True,
        "source_urls_exposed":False,
        "filesystem_paths_exposed":False,
        "agent_brain_context":True,
        "runtime_events":True,
        "universal_agent_control":True,
        "homeserver_execution_authority":True,
    }

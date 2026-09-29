from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import hosting_runtime

CONTRACT="vp3.hosting.observability.v2"
MAX_RECENT_EVENTS=500
MAX_LOG_BYTES=512*1024
_LOCK=threading.RLock()
_TOKENISH=re.compile(r"^(?:[A-Fa-f0-9]{24,}|[A-Za-z0-9_-]{32,})$")


class ObservabilityError(hosting_runtime.HostingError):
    pass


def _root(site_id:str)->Path:
    root=hosting_runtime.site_root(site_id)/"observability"
    root.mkdir(parents=True,exist_ok=True)
    hosting_runtime._ensure_no_symlink(root)
    return root


def _summary_path(site_id:str)->Path:
    return _root(site_id)/"summary.json"


def _log_path(site_id:str)->Path:
    return _root(site_id)/"requests.jsonl"


def _empty(site_id:str)->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "site_id":site_id,
        "requests_total":0,
        "success_total":0,
        "client_error_total":0,
        "server_error_total":0,
        "bytes_out_total":0,
        "duration_ms_total":0.0,
        "duration_ms_max":0.0,
        "last_request_at":None,
        "status_counts":{},
        "method_counts":{},
    }


def _load_summary(site_id:str)->dict[str,Any]:
    hosting_runtime.get_site(site_id)
    path=_summary_path(site_id)
    if not path.is_file():
        return _empty(site_id)
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise ObservabilityError("Hosting observability summary is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("contract")!=CONTRACT or payload.get("site_id")!=site_id:
        raise ObservabilityError("Hosting observability summary is invalid.",500)
    return payload


def _write_summary(site_id:str,payload:dict[str,Any])->None:
    path=_summary_path(site_id)
    temporary=path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(temporary,path)


def _sanitize_path(value:str)->str:
    raw=str(value or "").replace("\\","/").split("?",1)[0].split("#",1)[0]
    if not raw.startswith("/"):
        raw="/"+raw.lstrip("/")
    parts=[]
    for part in raw.split("/"):
        if not part or part==".":
            continue
        if part=="..":
            parts.append("_")
            continue
        if "@" in part or _TOKENISH.fullmatch(part):
            parts.append("[redacted]")
            continue
        parts.append(part[:120])
    result="/"+"/".join(parts)
    return result[:500] or "/"


def record(
    site_id:str,
    *,
    method:str,
    request_path:str,
    status_code:int,
    duration_ms:float,
    bytes_out:int=0,
    runtime_kind:str|None=None,
    release_id:str|None=None,
    error_code:str|None=None,
)->dict[str,Any]:
    hosting_runtime.get_site(site_id)
    status=max(100,min(599,int(status_code)))
    duration=max(0.0,float(duration_ms))
    size=max(0,int(bytes_out))
    event={
        "at":datetime.now(timezone.utc).isoformat(),
        "method":str(method or "GET").upper()[:12],
        "path":_sanitize_path(request_path),
        "status":status,
        "duration_ms":round(duration,3),
        "bytes_out":size,
        "runtime":str(runtime_kind or "")[:20] or None,
        "release_id":str(release_id or "")[:80] or None,
        "error_code":str(error_code or "")[:80] or None,
    }
    with _LOCK:
        summary=_load_summary(site_id)
        summary["requests_total"]=int(summary.get("requests_total") or 0)+1
        if status<400:
            summary["success_total"]=int(summary.get("success_total") or 0)+1
        elif status<500:
            summary["client_error_total"]=int(summary.get("client_error_total") or 0)+1
        else:
            summary["server_error_total"]=int(summary.get("server_error_total") or 0)+1
        summary["bytes_out_total"]=int(summary.get("bytes_out_total") or 0)+size
        summary["duration_ms_total"]=round(float(summary.get("duration_ms_total") or 0.0)+duration,3)
        summary["duration_ms_max"]=round(max(float(summary.get("duration_ms_max") or 0.0),duration),3)
        summary["last_request_at"]=event["at"]
        statuses=dict(summary.get("status_counts") or {})
        statuses[str(status)]=int(statuses.get(str(status)) or 0)+1
        summary["status_counts"]=statuses
        methods=dict(summary.get("method_counts") or {})
        methods[event["method"]]=int(methods.get(event["method"]) or 0)+1
        summary["method_counts"]=methods
        _write_summary(site_id,summary)

        path=_log_path(site_id)
        with path.open("a",encoding="utf-8",newline="\n") as handle:
            handle.write(json.dumps(event,separators=(",",":"),sort_keys=True)+"\n")
        _trim_log(site_id)
    return event


def _trim_log(site_id:str)->None:
    path=_log_path(site_id)
    try:
        if path.stat().st_size<=MAX_LOG_BYTES:
            return
        lines=path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    kept=lines[-MAX_RECENT_EVENTS:]
    while kept and len(("\n".join(kept)+"\n").encode("utf-8"))>MAX_LOG_BYTES:
        kept.pop(0)
    temporary=path.with_suffix(".tmp")
    temporary.write_text(("\n".join(kept)+"\n") if kept else "",encoding="utf-8")
    os.replace(temporary,path)


def summary(site_id:str)->dict[str,Any]:
    with _LOCK:
        payload=_load_summary(site_id)
    total=int(payload.get("requests_total") or 0)
    duration=float(payload.get("duration_ms_total") or 0.0)
    return {
        **payload,
        "average_duration_ms":round(duration/total,3) if total else 0.0,
        "error_rate":round((int(payload.get("client_error_total") or 0)+int(payload.get("server_error_total") or 0))/total,6) if total else 0.0,
    }


def recent(site_id:str,limit:int=50)->dict[str,Any]:
    hosting_runtime.get_site(site_id)
    bounded=max(1,min(200,int(limit)))
    path=_log_path(site_id)
    items=[]
    if path.is_file():
        try:
            lines=path.read_text(encoding="utf-8").splitlines()[-bounded:]
        except (OSError,UnicodeDecodeError) as exc:
            raise ObservabilityError("Hosting request log is unreadable.",500) from exc
        for line in lines:
            try:
                item=json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item,dict):
                items.append(item)
    items.reverse()
    return {"contract":CONTRACT,"site_id":site_id,"items":items,"limit":bounded}


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "aggregate_metrics":True,
        "bounded_recent_request_log":True,
        "max_recent_events":MAX_RECENT_EVENTS,
        "max_log_bytes":MAX_LOG_BYTES,
        "query_strings_logged":False,
        "request_bodies_logged":False,
        "headers_logged":False,
        "cookies_logged":False,
        "tokens_logged":False,
        "filesystem_paths_logged":False,
        "raw_sql_logged":False,
    }

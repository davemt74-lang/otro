from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi.responses import FileResponse, Response

from ..config import settings
from . import homeserver_app_resources, homeserver_app_security, homeserver_apps

CONTRACT="vp3.app.runtime-services.v1"
JOBS_CONTRACT="vp3.app.jobs.v1"
EVENTS_CONTRACT="vp3.app.events.v1"
MAX_EVENT_BYTES=32*1024
MAX_JOB_OUTPUT_BYTES=1024*1024
PHP_TIMEOUT_SECONDS=15
JOB_LOOP_SECONDS=5
_TOPIC=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,159}$")
_JOB_ID=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
_ALLOWED_METHODS={"GET","HEAD","POST"}
_STOP=threading.Event()
_THREAD:threading.Thread|None=None
_LOCK=threading.RLock()


class AppRuntimeError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe_rel(value:str)->PurePosixPath:
    raw=str(value or "").replace("\\","/").lstrip("/")
    if not raw:
        return PurePosixPath(".")
    rel=PurePosixPath(raw)
    if rel.is_absolute() or any(part in {"",".."} for part in rel.parts):
        raise AppRuntimeError("App runtime path is invalid.")
    if rel.parts and ":" in rel.parts[0]:
        raise AppRuntimeError("App runtime path is invalid.")
    return rel


def _runtime_db(app_key:str)->Path:
    return homeserver_app_resources.sqlite_path(app_key,"vp3-runtime.db")


def _connect(app_key:str)->sqlite3.Connection:
    path=_runtime_db(app_key)
    connection=sqlite3.connect(path,timeout=10)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS vp3_app_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'app',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_vp3_app_events_topic_id ON vp3_app_events(topic,id DESC);
        CREATE TABLE IF NOT EXISTS vp3_app_jobs(
            job_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            interval_seconds INTEGER NOT NULL,
            action_json TEXT NOT NULL,
            next_run_at INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS vp3_app_job_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            status TEXT NOT NULL,
            output_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );
        """
    )
    return connection


def _app_runtime_root(app_key:str)->Path:
    app=homeserver_apps.get(app_key)
    release_id=str((app.get("metadata") or {}).get("active_release_id") or "")
    if not release_id.startswith("apprel_"):
        raise AppRuntimeError("App has no active runtime release.",409)
    base=(settings.data_dir/"app-runtime"/app_key/"releases").resolve()
    root=(base/release_id/"content").resolve()
    if base not in root.parents or not root.is_dir():
        raise AppRuntimeError("Active app runtime release is unavailable.",500)
    return root


def _manifest(app_key:str)->dict[str,Any]:
    path=_app_runtime_root(app_key)/"vp3-app.json"
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppRuntimeError("Active app manifest is unavailable.",500) from exc
    if not isinstance(value,dict) or value.get("app_key")!=app_key:
        raise AppRuntimeError("Active app manifest identity is invalid.",500)
    return value


def _load_contract_file(root:Path,relative:str,contract:str)->dict[str,Any]:
    rel=_safe_rel(relative)
    target=(root/Path(*rel.parts)).resolve()
    if root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
        raise AppRuntimeError("App runtime contract file is unavailable.",500)
    try:
        value=json.loads(target.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppRuntimeError("App runtime contract file is invalid.",400) from exc
    if not isinstance(value,dict) or value.get("contract")!=contract:
        raise AppRuntimeError(f"App runtime contract must be {contract}.")
    return value


def validate_release_contracts(app_key:str,content_root:Path)->dict[str,Any]:
    root=content_root.resolve()
    manifest_path=root/"vp3-app.json"
    try:
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppRuntimeError("Installed app manifest is unavailable.",400) from exc
    if not isinstance(manifest,dict) or manifest.get("app_key")!=app_key:
        raise AppRuntimeError("Installed app manifest identity mismatch.",400)
    jobs_path=str(manifest.get("jobs") or "").strip()
    events_path=str(manifest.get("events") or "").strip()
    jobs=[]
    subscriptions=[]
    if jobs_path:
        payload=_load_contract_file(root,jobs_path,JOBS_CONTRACT)
        raw_jobs=payload.get("jobs",[])
        if not isinstance(raw_jobs,list) or len(raw_jobs)>64:
            raise AppRuntimeError("App jobs contract is invalid.")
        seen=set()
        for row in raw_jobs:
            if not isinstance(row,dict):
                raise AppRuntimeError("App job definition is invalid.")
            job_id=str(row.get("job_id") or "").strip()
            enabled=bool(row.get("enabled",True))
            try:
                interval=int(row.get("interval_seconds") or 0)
            except (TypeError,ValueError) as exc:
                raise AppRuntimeError("App job interval is invalid.") from exc
            action=row.get("action")
            if not _JOB_ID.fullmatch(job_id) or job_id in seen:
                raise AppRuntimeError("App job_id is invalid or duplicated.")
            seen.add(job_id)
            if interval<60 or interval>86400:
                raise AppRuntimeError("App job interval must be between 60 and 86400 seconds.")
            if not isinstance(action,dict):
                raise AppRuntimeError("App job action must be an object.")
            kind=str(action.get("type") or "")
            if kind not in {"event.emit","php.script","agent.prompt"}:
                raise AppRuntimeError("App job action type is unsupported.")
            if kind=="event.emit":
                topic=str(action.get("topic") or "")
                if not _TOPIC.fullmatch(topic):
                    raise AppRuntimeError("App job event topic is invalid.")
                raw_payload=json.dumps(action.get("payload") if isinstance(action.get("payload"),dict) else {},separators=(",",":"),sort_keys=True)
                if len(raw_payload.encode("utf-8"))>MAX_EVENT_BYTES:
                    raise AppRuntimeError("App job event payload exceeds the size limit.")
            if kind=="agent.prompt":
                prompt=str(action.get("prompt") or "").strip()
                if not prompt or len(prompt)>32000:
                    raise AppRuntimeError("App Agent job prompt is invalid.")
                system_prompt=str(action.get("system_prompt") or "")
                if len(system_prompt)>4000:
                    raise AppRuntimeError("App Agent job system prompt is too long.")
                context_keys=action.get("context_keys") or []
                if not isinstance(context_keys,list) or len(context_keys)>16 or any(
                    not isinstance(value,str) or not value.strip() or len(value)>80 for value in context_keys
                ):
                    raise AppRuntimeError("App Agent job context_keys are invalid.")
            if kind=="php.script":
                script=_safe_rel(str(action.get("script") or ""))
                target=(root/Path(*script.parts)).resolve()
                if root not in target.parents or not target.is_file() or target.suffix.lower()!=".php":
                    raise AppRuntimeError("App PHP job script is unavailable.")
                if str(manifest.get("runtime") or "")!="php":
                    raise AppRuntimeError("PHP jobs require a PHP app runtime.")
            jobs.append({"job_id":job_id,"enabled":enabled,"interval_seconds":interval,"action":action})
    if events_path:
        payload=_load_contract_file(root,events_path,EVENTS_CONTRACT)
        raw=payload.get("subscriptions",[])
        if not isinstance(raw,list) or len(raw)>64:
            raise AppRuntimeError("App event subscriptions are invalid.")
        for topic in raw:
            value=str(topic or "").strip()
            if not _TOPIC.fullmatch(value):
                raise AppRuntimeError("App event subscription topic is invalid.")
            if value not in subscriptions:
                subscriptions.append(value)
    return {"jobs":jobs,"subscriptions":subscriptions}


def sync_release(app_key:str,content_root:Path|None=None)->dict[str,Any]:
    root=(content_root or _app_runtime_root(app_key)).resolve()
    manifest_path=root/"vp3-app.json"
    try:
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppRuntimeError("Installed app manifest is unavailable.",500) from exc
    if manifest.get("app_key")!=app_key:
        raise AppRuntimeError("Installed app manifest identity mismatch.",500)
    contracts=validate_release_contracts(app_key,root)
    jobs=contracts["jobs"]
    subscriptions=contracts["subscriptions"]
    now=int(time.time())
    connection=_connect(app_key)
    try:
        existing={row["job_id"]:dict(row) for row in connection.execute("SELECT * FROM vp3_app_jobs").fetchall()}
        keep=set()
        for job in jobs:
            keep.add(job["job_id"])
            previous=existing.get(job["job_id"])
            next_run=int(previous["next_run_at"]) if previous else now+job["interval_seconds"]
            connection.execute(
                """INSERT INTO vp3_app_jobs(job_id,enabled,interval_seconds,action_json,next_run_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET enabled=excluded.enabled,
                   interval_seconds=excluded.interval_seconds,action_json=excluded.action_json,
                   updated_at=CURRENT_TIMESTAMP""",
                (job["job_id"],1 if job["enabled"] else 0,job["interval_seconds"],json.dumps(job["action"],separators=(",",":"),sort_keys=True),next_run),
            )
        for job_id in set(existing)-keep:
            connection.execute("DELETE FROM vp3_app_jobs WHERE job_id=?",(job_id,))
        connection.commit()
    finally:
        connection.close()
    app=homeserver_apps.get(app_key)
    metadata=dict(app.get("metadata") or {})
    metadata["runtime_services"]={"jobs":len(jobs),"subscriptions":subscriptions,"contract":CONTRACT}
    from ..database import db
    with db() as central:
        central.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?",
            (json.dumps(metadata,separators=(",",":"),sort_keys=True),app["app_id"]),
        )
        central.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.runtime.synced','system','runtime',?)""",
            (app["app_id"],json.dumps({"jobs":len(jobs),"subscriptions":subscriptions},separators=(",",":"),sort_keys=True)),
        )
    return runtime_status(app_key)


def publish_event(app_key:str,topic:str,payload:dict[str,Any]|None=None,*,source:str="app")->dict[str,Any]:
    homeserver_apps.get(app_key)
    name=str(topic or "").strip()
    if not _TOPIC.fullmatch(name):
        raise AppRuntimeError("Event topic is invalid.")
    body=payload if isinstance(payload,dict) else {}
    raw=json.dumps(body,separators=(",",":"),sort_keys=True)
    if len(raw.encode("utf-8"))>MAX_EVENT_BYTES:
        raise AppRuntimeError("Event payload exceeds the size limit.",413)
    connection=_connect(app_key)
    try:
        cursor=connection.execute(
            "INSERT INTO vp3_app_events(topic,payload_json,source) VALUES (?,?,?)",
            (name,raw,str(source or "app")[:40]),
        )
        event_id=int(cursor.lastrowid)
        connection.commit()
        row=connection.execute("SELECT * FROM vp3_app_events WHERE id=?",(event_id,)).fetchone()
    finally:
        connection.close()
    homeserver_app_resources.enforce_sqlite_quota(app_key)
    item=dict(row)
    item["payload"]=json.loads(item.pop("payload_json"))
    return item


def list_events(app_key:str,*,topic:str="",limit:int=100)->list[dict[str,Any]]:
    homeserver_apps.get(app_key)
    bounded=max(1,min(500,int(limit)))
    connection=_connect(app_key)
    try:
        if topic:
            if not _TOPIC.fullmatch(topic):
                raise AppRuntimeError("Event topic is invalid.")
            rows=connection.execute(
                "SELECT * FROM vp3_app_events WHERE topic=? ORDER BY id DESC LIMIT ?",(topic,bounded)
            ).fetchall()
        else:
            rows=connection.execute("SELECT * FROM vp3_app_events ORDER BY id DESC LIMIT ?",(bounded,)).fetchall()
    finally:
        connection.close()
    out=[]
    for row in rows:
        item=dict(row)
        item["payload"]=json.loads(item.pop("payload_json"))
        out.append(item)
    return out


def list_jobs(app_key:str)->list[dict[str,Any]]:
    homeserver_apps.get(app_key)
    connection=_connect(app_key)
    try:
        rows=connection.execute("SELECT * FROM vp3_app_jobs ORDER BY job_id").fetchall()
    finally:
        connection.close()
    out=[]
    for row in rows:
        item=dict(row)
        item["enabled"]=bool(item["enabled"])
        item["action"]=json.loads(item.pop("action_json"))
        out.append(item)
    return out


def _safe_env(app_key:str)->dict[str,str]:
    env={}
    for key in ("SYSTEMROOT","WINDIR","COMSPEC","TEMP","TMP","TMPDIR"):
        value=os.environ.get(key)
        if value:
            env[key]=value
    env["VP3_APP_KEY"]=app_key
    env["VP3_APP_STORAGE_DIR"]=str(homeserver_app_resources.files_root(app_key).resolve())
    env["VP3_APP_SQLITE_PATH"]=str(homeserver_app_resources.sqlite_path(app_key,"app.db").resolve())
    status=homeserver_app_security.secret_status(app_key)
    for key in status["configured_keys"]:
        value=homeserver_app_security.get_secret(app_key,key)
        if value is not None:
            env[f"VP3_SECRET_{key}"]=value
    return env


def _run_action(app_key:str,action:dict[str,Any])->dict[str,Any]:
    kind=str(action.get("type") or "")
    if kind=="event.emit":
        event=publish_event(app_key,str(action.get("topic") or ""),action.get("payload") if isinstance(action.get("payload"),dict) else {},source="job")
        return {"event_id":event["id"],"topic":event["topic"]}
    if kind=="agent.prompt":
        from . import homeserver_app_agent_runtime
        try:
            result=homeserver_app_agent_runtime.run_prompt(
                app_key,
                str(action.get("prompt") or ""),
                context_keys=list(action.get("context_keys") or []),
                system_prompt=str(action.get("system_prompt") or ""),
                job_id=str(action.get("job_id") or ""),
            )
        except homeserver_app_agent_runtime.AppAgentRuntimeError as exc:
            raise AppRuntimeError(str(exc),exc.status_code) from exc
        return {
            "run_key":result["run_key"],
            "status":result["status"],
            "compute_source":result["compute_source"],
            "provider_key":result["provider_key"],
            "model":result["model"],
            "usage":result["usage"],
            "content":result["content"],
        }
    if kind=="php.script":
        root=_app_runtime_root(app_key)
        rel=_safe_rel(str(action.get("script") or ""))
        target=(root/Path(*rel.parts)).resolve()
        if root not in target.parents or not target.is_file() or target.suffix.lower()!=".php":
            raise AppRuntimeError("PHP job script is unavailable.")
        binary=shutil.which("php")
        if not binary:
            raise AppRuntimeError("PHP CLI runtime is not installed.",503)
        data_root=(settings.data_dir/"app-data"/homeserver_apps.get(app_key)["app_id"]).resolve()
        open_basedir=os.pathsep.join([str(root),str(data_root),str(Path(os.environ.get("TEMP") or os.environ.get("TMP") or root).resolve())])
        disabled="exec,shell_exec,system,passthru,proc_open,popen,pcntl_exec"
        try:
            completed=subprocess.run(
                [binary,"-d",f"open_basedir={open_basedir}","-d",f"disable_functions={disabled}",str(target)],
                cwd=root,env=_safe_env(app_key),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                timeout=PHP_TIMEOUT_SECONDS,check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppRuntimeError("App PHP job exceeded the execution timeout.",504) from exc
        except OSError as exc:
            raise AppRuntimeError("App PHP job runtime could not start.",503) from exc
        if completed.returncode!=0:
            raise AppRuntimeError("App PHP job failed.",502)
        output=completed.stdout[:MAX_JOB_OUTPUT_BYTES].decode("utf-8","replace")
        return {"stdout":output,"truncated":len(completed.stdout)>MAX_JOB_OUTPUT_BYTES}
    raise AppRuntimeError("App job action type is unsupported.")


def run_job(app_key:str,job_id:str)->dict[str,Any]:
    if not _JOB_ID.fullmatch(str(job_id or "")):
        raise AppRuntimeError("App job identifier is invalid.")
    connection=_connect(app_key)
    try:
        row=connection.execute("SELECT * FROM vp3_app_jobs WHERE job_id=?",(job_id,)).fetchone()
        if row is None:
            raise AppRuntimeError("App job not found.",404)
        cursor=connection.execute(
            "INSERT INTO vp3_app_job_runs(job_id,status) VALUES (?,'running')",(job_id,)
        )
        run_id=int(cursor.lastrowid)
        connection.commit()
        action=json.loads(row["action_json"])
        action["job_id"]=job_id
        try:
            output=_run_action(app_key,action)
            status="succeeded"
            error=None
        except Exception as exc:
            output={}
            status="failed"
            error=str(exc)[:1000]
        next_run=int(time.time())+int(row["interval_seconds"])
        connection.execute(
            "UPDATE vp3_app_jobs SET next_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
            (next_run,job_id),
        )
        connection.execute(
            """UPDATE vp3_app_job_runs SET status=?,output_json=?,error=?,completed_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (status,json.dumps(output,separators=(",",":"),sort_keys=True),error,run_id),
        )
        connection.commit()
    finally:
        connection.close()
    homeserver_app_resources.enforce_sqlite_quota(app_key)
    if status=="failed":
        raise AppRuntimeError(error or "App job failed.",502)
    return {"run_id":run_id,"job_id":job_id,"status":status,"output":output,"next_run_at":next_run}


def run_due_jobs()->int:
    from ..database import db
    with db() as central:
        rows=central.execute(
            "SELECT app_key FROM homeserver_apps WHERE app_class='user' AND lifecycle_state='running' ORDER BY app_key"
        ).fetchall()
    now=int(time.time())
    count=0
    for row in rows:
        app_key=str(row["app_key"])
        try:
            connection=_connect(app_key)
            due=connection.execute(
                "SELECT job_id FROM vp3_app_jobs WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at LIMIT 8",(now,)
            ).fetchall()
            connection.close()
            for job in due:
                try:
                    run_job(app_key,str(job["job_id"]))
                except Exception:
                    pass
                count+=1
        except Exception:
            continue
    return count


def _loop()->None:
    while not _STOP.wait(JOB_LOOP_SECONDS):
        run_due_jobs()


def start()->None:
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD=threading.Thread(target=_loop,name="vp3-app-runtime-jobs",daemon=True)
        _THREAD.start()


def stop()->None:
    global _THREAD
    _STOP.set()
    thread=_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2)
    _THREAD=None


def _resolve_target(app_key:str,request_path:str)->Path:
    root=_app_runtime_root(app_key)
    rel=_safe_rel(request_path)
    if rel.as_posix()!="." and (rel.parts[0] in {"agent","runtime","database"} or rel.as_posix() in {"vp3-app.json","settings.schema.json","README.md"}):
        raise AppRuntimeError("Requested app resource is not web-exposed.",403)
    target=root if rel.as_posix()=="." else (root/Path(*rel.parts)).resolve()
    if target!=root and root not in target.parents:
        raise AppRuntimeError("Requested app path escaped the active release.")
    if target.exists() and target.is_symlink():
        raise AppRuntimeError("App symlinks are not served.",403)
    if target.is_dir():
        for name in ("index.html","index.htm","index.php"):
            candidate=target/name
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        raise AppRuntimeError("App directory has no index document.",404)
    if not target.is_file():
        raise AppRuntimeError("App resource not found.",404)
    return target


def _parse_cgi(raw:bytes)->tuple[int,dict[str,str],bytes]:
    marker=b"\r\n\r\n"
    split=raw.find(marker)
    if split<0:
        marker=b"\n\n"
        split=raw.find(marker)
    if split<0:
        raise AppRuntimeError("PHP runtime returned an invalid CGI response.",502)
    headers={}
    status=200
    for line in raw[:split].decode("latin-1","replace").replace("\r\n","\n").split("\n"):
        if ":" not in line:
            continue
        name,value=line.split(":",1)
        name=name.strip()
        value=value.strip()
        if name.lower()=="status":
            try:
                status=int(value.split(" ",1)[0])
            except Exception as exc:
                raise AppRuntimeError("PHP runtime returned an invalid status.",502) from exc
        elif name.lower() not in {"connection","transfer-encoding","content-length","set-cookie"}:
            headers[name]=value
    return status,headers,raw[split+len(marker):]


def serve(app_key:str,request_path:str,*,method:str="GET",query_string:str="",content_type:str|None=None,body:bytes=b""):
    app=homeserver_apps.get(app_key)
    if app["lifecycle_state"]!="running":
        raise AppRuntimeError("App is not running.",503)
    method=str(method or "GET").upper()
    if method not in _ALLOWED_METHODS:
        raise AppRuntimeError("App runtime method is not allowed.",405)
    target=_resolve_target(app_key,request_path)
    manifest=_manifest(app_key)
    if target.suffix.lower()==".php":
        if manifest.get("runtime")!="php":
            raise AppRuntimeError("PHP execution is not enabled for this app.",403)
        binary=shutil.which("php-cgi")
        if not binary:
            raise AppRuntimeError("PHP CGI runtime is not installed.",503)
        if len(body)>8*1024*1024:
            raise AppRuntimeError("App request body exceeds the runtime limit.",413)
        root=_app_runtime_root(app_key)
        data_root=(settings.data_dir/"app-data"/app["app_id"]).resolve()
        open_basedir=os.pathsep.join([str(root),str(data_root),str(Path(os.environ.get("TEMP") or os.environ.get("TMP") or root).resolve())])
        disabled="exec,shell_exec,system,passthru,proc_open,popen,pcntl_exec"
        env=_safe_env(app_key)
        env.update({
            "GATEWAY_INTERFACE":"CGI/1.1","SERVER_PROTOCOL":"HTTP/1.1","SERVER_SOFTWARE":"VP3-HomeServer-App",
            "REQUEST_METHOD":method,"QUERY_STRING":query_string,"SCRIPT_FILENAME":str(target),
            "SCRIPT_NAME":"/"+str(request_path or target.name).lstrip("/"),"DOCUMENT_ROOT":str(root),
            "REDIRECT_STATUS":"1","CONTENT_LENGTH":str(len(body)),
        })
        if content_type:
            env["CONTENT_TYPE"]=content_type
        try:
            completed=subprocess.run(
                [binary,"-d",f"open_basedir={open_basedir}","-d",f"disable_functions={disabled}"],
                input=body,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=root,env=env,
                timeout=5,check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppRuntimeError("App PHP request exceeded the execution timeout.",504) from exc
        if completed.returncode!=0:
            raise AppRuntimeError("App PHP runtime failed.",502)
        status,headers,response_body=_parse_cgi(completed.stdout)
        media=headers.pop("Content-Type",headers.pop("content-type",None))
        headers.setdefault("Cache-Control","no-store")
        headers.setdefault("X-Content-Type-Options","nosniff")
        return Response(content=b"" if method=="HEAD" else response_body,status_code=status,headers=headers,media_type=media)
    if method=="POST":
        raise AppRuntimeError("POST requests require a PHP entrypoint.",405)
    media,_=mimetypes.guess_type(str(target))
    return FileResponse(target,media_type=media or "application/octet-stream",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


def runtime_status(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    metadata=dict(app.get("metadata") or {})
    services=dict(metadata.get("runtime_services") or {})
    return {
        "contract":CONTRACT,
        "app_key":app_key,
        "state":app["lifecycle_state"],
        "local_route":f"/api/v1/control/homeserver-apps/{app_key}/preview/",
        "jobs":list_jobs(app_key),
        "subscriptions":list(services.get("subscriptions") or []),
        "event_count":len(list_events(app_key,limit=500)),
        "php_cgi_available":bool(shutil.which("php-cgi")),
        "php_cli_available":bool(shutil.which("php")),
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "local_owner_preview":True,
        "static_serving":True,
        "php_cgi":bool(shutil.which("php-cgi")),
        "php_open_basedir":True,
        "php_dangerous_functions_disabled":True,
        "durable_app_events":True,
        "interval_jobs":True,
        "job_action_types":["event.emit","php.script","agent.prompt"],
        "arbitrary_shell_commands":False,
        "max_event_bytes":MAX_EVENT_BYTES,
        "minimum_job_interval_seconds":60,
    }

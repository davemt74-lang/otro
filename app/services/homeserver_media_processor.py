from __future__ import annotations
import json, os, secrets, shutil, sqlite3, subprocess, threading, time, uuid
from pathlib import Path
from typing import Any
from . import homeserver_app_resources, homeserver_app_runtime, homeserver_app_security, homeserver_apps, homeserver_media_server, homeserver_media_tools

APP_KEY="vp3.media-processor"
CONTRACT="vp3.media-processor.v1"
_LOCK=threading.RLock()
_WORKER=None
_STOP=threading.Event()

class MediaProcessorError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message); self.status_code=status_code

def _ensure()->dict[str,Any]:
    try: app=homeserver_apps.get(APP_KEY)
    except Exception as exc: raise MediaProcessorError("VP3 Media Processor is not installed.",404) from exc
    if not app.get("installed_version"): raise MediaProcessorError("VP3 Media Processor is not installed.",409)
    return app

def _connect()->sqlite3.Connection:
    _ensure()
    c=sqlite3.connect(homeserver_app_resources.sqlite_path(APP_KEY,"media-processor.db"),timeout=20,check_same_thread=False)
    c.row_factory=sqlite3.Row; c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS processor_destinations(
      destination_id TEXT PRIMARY KEY, label TEXT NOT NULL, root_path TEXT NOT NULL,
      destination_kind TEXT NOT NULL DEFAULT 'app_storage', enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS processor_jobs(
      job_id TEXT PRIMARY KEY, media_id TEXT NOT NULL, operation TEXT NOT NULL, preset TEXT NOT NULL,
      output_format TEXT NOT NULL, destination_id TEXT NOT NULL DEFAULT 'app-storage',
      status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
      eta_seconds INTEGER, duration_seconds REAL,
      output_rel TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_processor_jobs ON processor_jobs(status,priority DESC,created_at);
    CREATE TABLE IF NOT EXISTS processor_derivatives(
      derivative_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, media_id TEXT NOT NULL, kind TEXT NOT NULL,
      preset TEXT NOT NULL, format TEXT NOT NULL, destination_id TEXT NOT NULL DEFAULT 'app-storage',
      relative_path TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS processor_settings(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      max_concurrent INTEGER NOT NULL DEFAULT 1,
      max_threads INTEGER NOT NULL DEFAULT 2,
      max_output_bytes INTEGER NOT NULL DEFAULT 10737418240,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT OR IGNORE INTO processor_settings(singleton) VALUES (1);
    """)
    default_root=(homeserver_app_resources.files_root(APP_KEY)/"derivatives").resolve()
    default_root.mkdir(parents=True,exist_ok=True)
    c.execute(
      "INSERT OR IGNORE INTO processor_destinations(destination_id,label,root_path,destination_kind,enabled) VALUES ('app-storage','Processor Derivatives',?,'app_storage',1)",
      (str(default_root),),
    )
    derivative_columns={str(row["name"]) for row in c.execute("PRAGMA table_info(processor_derivatives)").fetchall()}
    if "destination_id" not in derivative_columns:
        c.execute("ALTER TABLE processor_derivatives ADD COLUMN destination_id TEXT NOT NULL DEFAULT 'app-storage'")
    job_columns={str(row["name"]) for row in c.execute("PRAGMA table_info(processor_jobs)").fetchall()}
    if "destination_id" not in job_columns:
        c.execute("ALTER TABLE processor_jobs ADD COLUMN destination_id TEXT NOT NULL DEFAULT 'app-storage'")
    if "eta_seconds" not in job_columns:
        c.execute("ALTER TABLE processor_jobs ADD COLUMN eta_seconds INTEGER")
    if "duration_seconds" not in job_columns:
        c.execute("ALTER TABLE processor_jobs ADD COLUMN duration_seconds REAL")
    columns={str(row["name"]) for row in c.execute("PRAGMA table_info(processor_settings)").fetchall()}
    if "max_output_bytes" not in columns:
        c.execute("ALTER TABLE processor_settings ADD COLUMN max_output_bytes INTEGER NOT NULL DEFAULT 10737418240")
        c.commit()
    return c

def settings()->dict[str,Any]:
    c=_connect()
    try:
      row=c.execute("SELECT * FROM processor_settings WHERE singleton=1").fetchone()
      return {"contract":CONTRACT,"settings":{
        "max_concurrent":int(row["max_concurrent"]),
        "max_threads":int(row["max_threads"]),
        "max_output_bytes":int(row["max_output_bytes"]),
      }}
    finally:c.close()

def update_settings(values:dict[str,Any])->dict[str,Any]:
    allowed={"max_concurrent","max_threads","max_output_bytes"}
    unknown=set(values)-allowed
    if unknown: raise MediaProcessorError(f"Unknown Media Processor setting: {sorted(unknown)[0]}")
    current=settings()["settings"]
    if "max_concurrent" in values:
      n=int(values["max_concurrent"])
      if n<1 or n>4: raise MediaProcessorError("max_concurrent must be 1-4.")
      current["max_concurrent"]=n
    if "max_threads" in values:
      n=int(values["max_threads"])
      if n<1 or n>16: raise MediaProcessorError("max_threads must be 1-16.")
      current["max_threads"]=n
    if "max_output_bytes" in values:
      n=int(values["max_output_bytes"])
      if n<1024*1024 or n>100*1024*1024*1024: raise MediaProcessorError("max_output_bytes is out of range.")
      current["max_output_bytes"]=n
    c=_connect()
    try:
      c.execute("UPDATE processor_settings SET max_concurrent=?,max_threads=?,max_output_bytes=?,updated_at=CURRENT_TIMESTAMP WHERE singleton=1",
        (current["max_concurrent"],current["max_threads"],current["max_output_bytes"]))
      c.commit()
    finally:c.close()
    return settings()

def capability()->dict[str,Any]:
    tools=homeserver_media_tools.public_capability()
    available=bool(tools["healthy"])
    return {"contract":CONTRACT,"ffmpeg_available":available,"ffprobe_available":bool(tools["ffprobe_available"]),
      "ffmpeg_managed_by_homeserver":True,"ffmpeg_version":tools["ffmpeg_version"],
      "video_transcode":available,"audio_convert":available,"image_convert":available,
      "thumbnail_generation":available,"proxy_generation":available,
      "source_media_owned":False,"source_media_deleted":False,"homeserver_execution_authority":True,
      "private_hosted_access_key":True,
      "resource_limits":True,"atomic_derivatives":True,"restart_recovery":True,
      "progress_eta":True,"active_cancellation":True,"derivative_file_access":True}

def _public(row)->dict[str,Any]:
    d=dict(row); d["progress"]=float(d.get("progress") or 0); d["priority"]=int(d.get("priority") or 0)
    d["eta_seconds"]=None if d.get("eta_seconds") is None else int(d["eta_seconds"])
    d["duration_seconds"]=None if d.get("duration_seconds") is None else float(d["duration_seconds"])
    d["filesystem_path_exposed"]=False; return d

def status()->dict[str,Any]:
    c=_connect()
    try:
      counts={r["status"]:int(r["n"]) for r in c.execute("SELECT status,COUNT(*) n FROM processor_jobs GROUP BY status")}
      deriv=int(c.execute("SELECT COUNT(*) FROM processor_derivatives").fetchone()[0])
    finally:c.close()
    return {"contract":CONTRACT,"counts":counts,"active":counts.get("processing",0),"queued":counts.get("queued",0),
      "failed":counts.get("failed",0),"completed":counts.get("completed",0),"derivatives":deriv,**capability()}

def list_jobs(limit:int=100)->dict[str,Any]:
    c=_connect()
    try: rows=c.execute("SELECT * FROM processor_jobs ORDER BY created_at DESC LIMIT ?",(max(1,min(500,int(limit))),)).fetchall()
    finally:c.close()
    return {"contract":CONTRACT,"jobs":[_public(r) for r in rows],"count":len(rows)}

def resolve_derivative(derivative_id:str)->tuple[Path,dict[str,Any]]:
    c=_connect()
    try:
        row=c.execute("SELECT * FROM processor_derivatives WHERE derivative_id=?",(str(derivative_id),)).fetchone()
    finally:c.close()
    if not row:
        raise MediaProcessorError("Derivative not found.",404)
    root=homeserver_app_resources.files_root(APP_KEY).resolve()
    rel=Path(str(row["relative_path"]))
    target=(root/rel).resolve()
    if root not in target.parents or not target.is_file() or target.is_symlink():
        raise MediaProcessorError("Derivative file is unavailable.",404)
    return target,dict(row)


def derivatives(media_id:str="",limit:int=200)->dict[str,Any]:
    c=_connect()
    try:
      if media_id: rows=c.execute("SELECT * FROM processor_derivatives WHERE media_id=? ORDER BY created_at DESC LIMIT ?",(media_id,max(1,min(500,int(limit))),)).fetchall()
      else: rows=c.execute("SELECT * FROM processor_derivatives ORDER BY created_at DESC LIMIT ?",(max(1,min(500,int(limit))),)).fetchall()
    finally:c.close()
    out=[]
    for r in rows:
      d=dict(r); d.pop("relative_path",None); d["filesystem_path_exposed"]=False; out.append(d)
    return {"contract":CONTRACT,"derivatives":out,"count":len(out)}

def _probe_duration(path:Path)->float|None:
    try:
        tools=homeserver_media_tools.require()
        result=subprocess.run(
            [str(tools["ffprobe"]),"-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],
            capture_output=True,text=True,timeout=30,check=False,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
        )
        if result.returncode!=0:
            return None
        value=float((result.stdout or "").strip())
        return value if value>0 else None
    except Exception:
        return None


def _job_state(job_id:str)->str:
    c=_connect()
    try:
        row=c.execute("SELECT status FROM processor_jobs WHERE job_id=?",(job_id,)).fetchone()
    finally:c.close()
    return str(row["status"]) if row else "cancelled"


def _update_progress(job_id:str,progress:float,eta:int|None,duration:float|None)->None:
    c=_connect()
    try:
        c.execute(
            "UPDATE processor_jobs SET progress=?,eta_seconds=?,duration_seconds=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
            (max(0.0,min(1.0,float(progress))),eta,duration,job_id),
        )
        c.commit()
    finally:c.close()


def _run_ffmpeg(job_id:str,cmd:list[str],duration:float|None)->None:
    command=[*cmd[:-1],"-progress","pipe:1","-nostats",cmd[-1]]
    try:
        process=subprocess.Popen(
            command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
        )
    except OSError as exc:
        raise MediaProcessorError("Managed FFmpeg process could not start.",503) from exc
    started=time.monotonic()
    try:
        if process.stdout is not None:
            for line in process.stdout:
                state=_job_state(job_id)
                if state=="cancelled":
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill()
                    raise MediaProcessorError("Processing job was cancelled.",409)
                try:
                    if str(homeserver_apps.get(APP_KEY).get("lifecycle_state") or "")!="running":
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill()
                        raise MediaProcessorError("Media Processor stopped while processing.",409)
                except MediaProcessorError:
                    raise
                except Exception:
                    pass
                raw=line.strip()
                if raw.startswith("out_time_ms=") and duration:
                    try:
                        seconds=float(raw.split("=",1)[1])/1_000_000
                        progress=max(0.0,min(0.99,seconds/duration))
                        elapsed=max(0.001,time.monotonic()-started)
                        eta=int(max(0,(elapsed/progress)-elapsed)) if progress>0.01 else None
                        _update_progress(job_id,progress,eta,duration)
                    except Exception:
                        pass
        code=process.wait(timeout=30)
    except Exception:
        if process.poll() is None:
            process.kill()
        raise
    if code!=0:
        raise MediaProcessorError("FFmpeg processing failed.",422)


def _spec(operation:str,preset:str,fmt:str,source_type:str)->tuple[list[str],str,str]:
    op=str(operation or "").strip().lower(); preset=str(preset or "default").strip().lower(); fmt=str(fmt or "").strip().lower()
    if op=="thumbnail":
      return (["-frames:v","1","-vf","scale='min(1280,iw)':-2"],fmt or "jpg","thumbnail")
    if op=="proxy":
      if source_type!="video": raise MediaProcessorError("Proxy generation requires video media.",409)
      scale={"editor":"1280","720p":"1280","1080p":"1920"}.get(preset,"1280")
      return (["-c:v","libx264","-preset","veryfast","-crf","28","-vf",f"scale='min({scale},iw)':-2","-c:a","aac","-b:a","128k"],fmt or "mp4","proxy")
    if op=="video.convert":
      if source_type!="video": raise MediaProcessorError("Video conversion requires video media.",409)
      scale={"720p":"1280","1080p":"1920","4k":"3840"}.get(preset)
      vf=["-vf",f"scale='min({scale},iw)':-2"] if scale else []
      quality={"small":"28","balanced":"23","high":"18"}.get(preset,"23")
      return (["-c:v","libx264","-preset","medium","-crf",quality,*vf,"-c:a","aac"],fmt or "mp4","video")
    if op=="audio.convert":
      if source_type not in {"audio","video"}: raise MediaProcessorError("Audio conversion requires audio or video media.",409)
      quality={"small":"6","balanced":"2","high":"0"}.get(preset,"2")
      return (["-vn","-c:a","libmp3lame","-q:a",quality],fmt or "mp3","audio")
    if op=="image.convert":
      if source_type!="image": raise MediaProcessorError("Image conversion requires image media.",409)
      scale={"small":"1280","medium":"2560","large":"4096"}.get(preset)
      vf=["-vf",f"scale='min({scale},iw)':-2"] if scale else []
      return (vf,fmt or "webp","image")
    raise MediaProcessorError("Unsupported processing operation.")

def enqueue(media_id:str,operation:str,preset:str="default",output_format:str="",priority:int=0)->dict[str,Any]:
    item=homeserver_media_server.item(str(media_id or ""))["item"]
    if not capability()["ffmpeg_available"]: raise MediaProcessorError("The HomeServer managed FFmpeg runtime is unavailable or unhealthy.",409)
    _spec(operation,preset,output_format,str(item["media_type"]))
    jid="proc_"+uuid.uuid4().hex
    c=_connect()
    try:
      c.execute("INSERT INTO processor_jobs(job_id,media_id,operation,preset,output_format,status,priority) VALUES (?,?,?,?,?,'queued',?)",
        (jid,media_id,operation,preset,output_format,max(-100,min(100,int(priority)))))
      c.commit()
    finally:c.close()
    homeserver_app_runtime.publish_event(APP_KEY,"processor.queued",{"job_id":jid,"media_id":media_id,"operation":operation},source="media-processor")
    _ensure_worker()
    return get_job(jid)

def get_job(job_id:str)->dict[str,Any]:
    c=_connect()
    try:r=c.execute("SELECT * FROM processor_jobs WHERE job_id=?",(job_id,)).fetchone()
    finally:c.close()
    if not r: raise MediaProcessorError("Processing job not found.",404)
    return {"contract":CONTRACT,"job":_public(r)}

def cancel(job_id:str)->dict[str,Any]:
    c=_connect()
    try:
      cur=c.execute("UPDATE processor_jobs SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND status IN ('queued','processing')",(job_id,))
      if cur.rowcount<1: raise MediaProcessorError("Processing job cannot be cancelled.",409)
      c.commit()
    finally:c.close()
    homeserver_app_runtime.publish_event(APP_KEY,"processor.cancelled",{"job_id":job_id},source="media-processor")
    return get_job(job_id)

def retry(job_id:str)->dict[str,Any]:
    c=_connect()
    try:
      cur=c.execute("UPDATE processor_jobs SET status='queued',progress=0,error='',updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND status IN ('failed','cancelled')",(job_id,))
      if cur.rowcount<1: raise MediaProcessorError("Processing job cannot be retried.",409)
      c.commit()
    finally:c.close()
    _ensure_worker(); return get_job(job_id)

def _run(job_id:str)->None:
    c=_connect()
    try:
      r=c.execute("SELECT * FROM processor_jobs WHERE job_id=?",(job_id,)).fetchone()
      if not r or r["status"]!="queued": return
      if c.execute("UPDATE processor_jobs SET status='processing',started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='queued'",(job_id,)).rowcount!=1: return
      c.commit(); job=dict(r)
    finally:c.close()
    try:
      src,mime,item=homeserver_media_server.resolve_stream(job["media_id"])
      args,fmt,kind=_spec(job["operation"],job["preset"],job["output_format"],item["media_type"])
      root=homeserver_app_resources.files_root(APP_KEY)/"derivatives"; root.mkdir(parents=True,exist_ok=True)
      name=f"{job_id}.{fmt}"; tmp=root/f".{job_id}.tmp.{fmt}"; final=root/name
      tools=homeserver_media_tools.require()
      limits=settings()["settings"]
      duration=_probe_duration(src)
      _update_progress(job_id,0.01,None,duration)
      cmd=[str(tools["ffmpeg"]),"-y","-threads",str(limits["max_threads"]),"-i",str(src),*args,str(tmp)]
      _run_ffmpeg(job_id,cmd,duration)
      if not tmp.is_file():
        raise MediaProcessorError("FFmpeg did not create the expected derivative.",502)
      size=tmp.stat().st_size
      if size>int(limits["max_output_bytes"]):
        raise MediaProcessorError("Generated derivative exceeds the configured maximum output size.",413)
      resource=homeserver_app_resources.resource_status(APP_KEY)
      used=max(0,int(resource["storage_used_bytes"])-size)
      if used+size>int(resource["storage_limit_bytes"]):
        raise MediaProcessorError("Media Processor app storage quota would be exceeded.",413)
      os.replace(tmp,final)
      did="deriv_"+uuid.uuid4().hex
      c=_connect()
      try:
        c.execute("INSERT INTO processor_derivatives(derivative_id,job_id,media_id,kind,preset,format,relative_path,size_bytes) VALUES (?,?,?,?,?,?,?,?)",
          (did,job_id,job["media_id"],kind,job["preset"],fmt,f"derivatives/{name}",final.stat().st_size))
        c.execute("UPDATE processor_jobs SET status='completed',progress=1,eta_seconds=0,output_rel=?,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",(f"derivatives/{name}",job_id))
        c.commit()
      finally:c.close()
      homeserver_app_runtime.publish_event(APP_KEY,"processor.completed",{"job_id":job_id,"media_id":job["media_id"],"derivative_id":did,"kind":kind},source="media-processor")
    except Exception as exc:
      try: tmp.unlink(missing_ok=True)
      except Exception: pass
      current=_job_state(job_id)
      c=_connect()
      try:
        if current=="cancelled":
          c.execute("UPDATE processor_jobs SET progress=0,eta_seconds=NULL,error='',updated_at=CURRENT_TIMESTAMP WHERE job_id=?",(job_id,))
          event="processor.cancelled"
        else:
          try:
            running=str(homeserver_apps.get(APP_KEY).get("lifecycle_state") or "")=="running"
          except Exception:
            running=False
          if not running:
            c.execute("UPDATE processor_jobs SET status='queued',progress=0,eta_seconds=NULL,error='Paused because Media Processor stopped.',updated_at=CURRENT_TIMESTAMP WHERE job_id=?",(job_id,))
            event="processor.requeued"
          else:
            c.execute("UPDATE processor_jobs SET status='failed',progress=0,eta_seconds=NULL,error=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",(str(exc)[:1200],job_id))
            event="processor.failed"
        c.commit()
      finally:c.close()
      homeserver_app_runtime.publish_event(APP_KEY,event,{"job_id":job_id,"error":str(exc)[:500]},source="media-processor")

def process_next()->dict[str,Any]|None:
    c=_connect()
    try:r=c.execute("SELECT job_id FROM processor_jobs WHERE status='queued' ORDER BY priority DESC,created_at LIMIT 1").fetchone()
    finally:c.close()
    if not r:return None
    _run(str(r["job_id"])); return get_job(str(r["job_id"]))

def _loop()->None:
    while not _STOP.wait(1):
      try:
        if str(homeserver_apps.get(APP_KEY).get("lifecycle_state") or "")!="running": continue
        max_concurrent=settings()["settings"]["max_concurrent"]
        c=_connect()
        try:
          active=int(c.execute("SELECT COUNT(*) FROM processor_jobs WHERE status='processing'").fetchone()[0])
          rows=c.execute("SELECT job_id FROM processor_jobs WHERE status='queued' ORDER BY priority DESC,created_at LIMIT ?",
            (max(0,max_concurrent-active),)).fetchall()
        finally:c.close()
        for row in rows:
          threading.Thread(target=_run,args=(str(row["job_id"]),),daemon=True).start()
      except Exception: continue

_RECOVERED=False

def _ensure_worker()->None:
    global _WORKER,_RECOVERED
    with _LOCK:
      if _WORKER and _WORKER.is_alive(): return
      if not _RECOVERED:
        recover_interrupted(); _RECOVERED=True
      _STOP.clear(); _WORKER=threading.Thread(target=_loop,daemon=True,name="vp3-media-processor"); _WORKER.start()

def stop_worker()->None:
    global _WORKER
    _STOP.set()
    if _WORKER and _WORKER.is_alive(): _WORKER.join(timeout=2)
    _WORKER=None

def recover_interrupted()->dict[str,Any]:
    c=_connect()
    try:
      n=c.execute("UPDATE processor_jobs SET status='queued',error='Recovered after HomeServer interruption.',progress=0,updated_at=CURRENT_TIMESTAMP WHERE status='processing'").rowcount
      c.commit()
    finally:c.close()
    return {"contract":CONTRACT,"recovered":int(n)}



def enable_remote()->dict[str,Any]:
    key=secrets.token_urlsafe(32)
    homeserver_app_security.set_secret(APP_KEY,"REMOTE_ACCESS_KEY",key)
    return {"contract":CONTRACT,"remote_enabled":True,"access_key":key,"access_key_returned_once":True}


def disable_remote()->dict[str,Any]:
    homeserver_app_security.remove_secret(APP_KEY,"REMOTE_ACCESS_KEY")
    return {"contract":CONTRACT,"remote_enabled":False}


def remote_status()->dict[str,Any]:
    configured="REMOTE_ACCESS_KEY" in set(homeserver_app_security.secret_status(APP_KEY).get("configured_keys") or [])
    return {"contract":CONTRACT,"remote_enabled":configured,"access_key_exposed":False}


def authenticate_remote(value:str)->bool:
    expected=homeserver_app_security.get_secret(APP_KEY,"REMOTE_ACCESS_KEY")
    return bool(expected and secrets.compare_digest(str(value or ""),expected))


def brain_context(limit:int=8)->dict[str,Any]:
    s=status(); jobs=list_jobs(limit)["jobs"]
    return {"contract":"vp3.media-processor.brain-context.v1","summary":{"active":s["active"],"queued":s["queued"],"failed":s["failed"],"completed":s["completed"],"derivatives":s["derivatives"]},
      "attention":[{"job_id":j["job_id"],"operation":j["operation"],"error":j["error"]} for j in jobs if j["status"]=="failed"][:8],
      "recent":[{k:j.get(k) for k in ("job_id","media_id","operation","preset","status","progress")} for j in jobs[:8]],
      "source_paths_exposed":False,"output_paths_exposed":False}

def invoke(action:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    a=dict(arguments or {}); k=str(action or "")
    if k=="processor.status": return status()
    if k=="processor.jobs": return list_jobs(int(a.get("limit",100)))
    if k=="processor.job.get": return get_job(str(a.get("job_id") or ""))
    if k=="processor.enqueue": return enqueue(str(a.get("media_id") or ""),str(a.get("operation") or ""),str(a.get("preset") or "default"),str(a.get("output_format") or ""),int(a.get("priority",0)))
    if k=="processor.cancel": return cancel(str(a.get("job_id") or ""))
    if k=="processor.retry": return retry(str(a.get("job_id") or ""))
    if k=="processor.derivatives": return derivatives(str(a.get("media_id") or ""),int(a.get("limit",200)))
    if k=="processor.brain-context": return brain_context(int(a.get("limit",8)))
    if k=="processor.settings": return settings()
    if k=="processor.settings.update": return update_settings(dict(a.get("values") or {}))
    raise MediaProcessorError("Unsupported Media Processor action.",404)

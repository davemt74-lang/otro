from __future__ import annotations
import json, os, shutil, sqlite3, subprocess, threading, time, uuid
from pathlib import Path
from typing import Any
from . import homeserver_app_resources, homeserver_app_runtime, homeserver_apps, homeserver_media_server

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
    CREATE TABLE IF NOT EXISTS processor_jobs(
      job_id TEXT PRIMARY KEY, media_id TEXT NOT NULL, operation TEXT NOT NULL, preset TEXT NOT NULL,
      output_format TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
      output_rel TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_processor_jobs ON processor_jobs(status,priority DESC,created_at);
    CREATE TABLE IF NOT EXISTS processor_derivatives(
      derivative_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, media_id TEXT NOT NULL, kind TEXT NOT NULL,
      preset TEXT NOT NULL, format TEXT NOT NULL, relative_path TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS processor_settings(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      max_concurrent INTEGER NOT NULL DEFAULT 1,
      max_threads INTEGER NOT NULL DEFAULT 2,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT OR IGNORE INTO processor_settings(singleton) VALUES (1);
    """)
    return c

def capability()->dict[str,Any]:
    ffmpeg=shutil.which("ffmpeg"); ffprobe=shutil.which("ffprobe")
    return {"contract":CONTRACT,"ffmpeg_available":bool(ffmpeg),"ffprobe_available":bool(ffprobe),
      "video_transcode":bool(ffmpeg),"audio_convert":bool(ffmpeg),"image_convert":bool(ffmpeg),
      "thumbnail_generation":bool(ffmpeg),"proxy_generation":bool(ffmpeg),
      "source_media_owned":False,"source_media_deleted":False,"homeserver_execution_authority":True}

def _public(row)->dict[str,Any]:
    d=dict(row); d["progress"]=float(d.get("progress") or 0); d["priority"]=int(d.get("priority") or 0)
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

def _spec(operation:str,preset:str,fmt:str,source_type:str)->tuple[list[str],str,str]:
    op=str(operation or "").strip().lower(); preset=str(preset or "default").strip().lower(); fmt=str(fmt or "").strip().lower()
    if op=="thumbnail":
      return (["-frames:v","1","-vf","scale='min(1280,iw)':-2"],fmt or "jpg","thumbnail")
    if op=="proxy":
      if source_type!="video": raise MediaProcessorError("Proxy generation requires video media.",409)
      return (["-c:v","libx264","-preset","veryfast","-crf","28","-vf","scale='min(1280,iw)':-2","-c:a","aac","-b:a","128k"],fmt or "mp4","proxy")
    if op=="video.convert":
      if source_type!="video": raise MediaProcessorError("Video conversion requires video media.",409)
      return (["-c:v","libx264","-preset","medium","-crf","23","-c:a","aac"],fmt or "mp4","video")
    if op=="audio.convert":
      if source_type not in {"audio","video"}: raise MediaProcessorError("Audio conversion requires audio or video media.",409)
      return (["-vn","-c:a","libmp3lame","-q:a","2"],fmt or "mp3","audio")
    if op=="image.convert":
      if source_type!="image": raise MediaProcessorError("Image conversion requires image media.",409)
      return ([],fmt or "webp","image")
    raise MediaProcessorError("Unsupported processing operation.")

def enqueue(media_id:str,operation:str,preset:str="default",output_format:str="",priority:int=0)->dict[str,Any]:
    item=homeserver_media_server.item(str(media_id or ""))["item"]
    if not capability()["ffmpeg_available"]: raise MediaProcessorError("FFmpeg is not available on this HomeServer.",409)
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
      name=f"{job_id}.{fmt}"; tmp=root/(name+".tmp"); final=root/name
      cmd=[shutil.which("ffmpeg") or "ffmpeg","-y","-i",str(src),*args,str(tmp)]
      result=subprocess.run(cmd,capture_output=True,text=True,timeout=7200)
      if result.returncode!=0: raise MediaProcessorError((result.stderr or "FFmpeg failed.")[-1200:],422)
      os.replace(tmp,final)
      homeserver_app_resources.resource_status(APP_KEY)
      did="deriv_"+uuid.uuid4().hex
      c=_connect()
      try:
        c.execute("INSERT INTO processor_derivatives(derivative_id,job_id,media_id,kind,preset,format,relative_path,size_bytes) VALUES (?,?,?,?,?,?,?,?)",
          (did,job_id,job["media_id"],kind,job["preset"],fmt,f"derivatives/{name}",final.stat().st_size))
        c.execute("UPDATE processor_jobs SET status='completed',progress=1,output_rel=?,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",(f"derivatives/{name}",job_id))
        c.commit()
      finally:c.close()
      homeserver_app_runtime.publish_event(APP_KEY,"processor.completed",{"job_id":job_id,"media_id":job["media_id"],"derivative_id":did,"kind":kind},source="media-processor")
    except Exception as exc:
      try: tmp.unlink(missing_ok=True)
      except Exception: pass
      c=_connect()
      try:
        c.execute("UPDATE processor_jobs SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",(str(exc)[:1200],job_id)); c.commit()
      finally:c.close()
      homeserver_app_runtime.publish_event(APP_KEY,"processor.failed",{"job_id":job_id,"error":str(exc)[:500]},source="media-processor")

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
        process_next()
      except Exception: continue

def _ensure_worker()->None:
    global _WORKER
    with _LOCK:
      if _WORKER and _WORKER.is_alive(): return
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
    raise MediaProcessorError("Unsupported Media Processor action.",404)

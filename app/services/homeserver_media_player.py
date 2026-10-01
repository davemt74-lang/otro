from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from . import homeserver_app_resources, homeserver_apps, homeserver_media_processor, homeserver_media_server

APP_KEY="vp3.media-player"
CONTRACT="vp3.media-player.v1"
_DIRECT_VIDEO={".mp4",".m4v",".webm"}
_DIRECT_AUDIO={".mp3",".m4a",".aac",".ogg",".opus",".wav"}


class MediaPlayerError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _ensure()->dict[str,Any]:
    try:
        app=homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise MediaPlayerError("VP3 Media Player is not installed.",404) from exc
    if not app.get("installed_version"):
        raise MediaPlayerError("VP3 Media Player is not installed.",409)
    return app


def _connect()->sqlite3.Connection:
    _ensure()
    c=sqlite3.connect(homeserver_app_resources.sqlite_path(APP_KEY,"media-player.db"),timeout=20,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS playback_devices(
      device_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'browser',
      capabilities_json TEXT NOT NULL DEFAULT '{}',
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
      last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS playback_sessions(
      session_id TEXT PRIMARY KEY,
      media_id TEXT NOT NULL,
      device_id TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'paused',
      delivery_mode TEXT NOT NULL DEFAULT 'direct',
      position_seconds REAL NOT NULL DEFAULT 0,
      duration_seconds REAL NOT NULL DEFAULT 0,
      processor_job_id TEXT NOT NULL DEFAULT '',
      error TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_playback_sessions_updated
      ON playback_sessions(updated_at DESC);
    CREATE TABLE IF NOT EXISTS playback_history(
      history_id TEXT PRIMARY KEY,
      media_id TEXT NOT NULL,
      session_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      position_seconds REAL NOT NULL DEFAULT 0,
      completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_playback_history_media
      ON playback_history(media_id,created_at DESC);
    """)
    return c


def _item(media_id:str)->dict[str,Any]:
    try:
        return homeserver_media_server.item(str(media_id or ""))["item"]
    except homeserver_media_server.MediaServerError as exc:
        raise MediaPlayerError(str(exc),exc.status_code) from exc


def _device_capabilities(value:dict[str,Any]|None)->dict[str,Any]:
    raw=dict(value or {})
    video=[str(x).lower()[:40] for x in list(raw.get("video") or [])[:32]]
    audio=[str(x).lower()[:40] for x in list(raw.get("audio") or [])[:32]]
    return {
        "video":video,
        "audio":audio,
        "max_height":max(0,min(int(raw.get("max_height") or 0),4320)),
        "direct_play":bool(raw.get("direct_play",True)),
    }


def register_device(name:str,kind:str="browser",capabilities:dict[str,Any]|None=None,device_id:str="")->dict[str,Any]:
    device_key=str(device_id or ("device_"+uuid.uuid4().hex))
    label=" ".join(str(name or "Playback Device").split())[:120] or "Playback Device"
    device_kind=str(kind or "browser").lower()
    if device_kind not in {"browser","tv","mobile","speaker","homeserver"}:
        raise MediaPlayerError("Unsupported playback device kind.")
    caps=_device_capabilities(capabilities)
    c=_connect()
    try:
        c.execute(
            """INSERT INTO playback_devices(device_id,name,kind,capabilities_json,active,last_seen_at)
               VALUES (?,?,?,?,1,CURRENT_TIMESTAMP)
               ON CONFLICT(device_id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
                 capabilities_json=excluded.capabilities_json,active=1,last_seen_at=CURRENT_TIMESTAMP""",
            (device_key,label,device_kind,json.dumps(caps,separators=(",",":"),sort_keys=True)),
        )
        c.commit()
    finally:
        c.close()
    return {"contract":CONTRACT,"device":device(device_key)["device"]}


def device(device_id:str)->dict[str,Any]:
    c=_connect()
    try:
        row=c.execute("SELECT * FROM playback_devices WHERE device_id=?",(str(device_id),)).fetchone()
    finally:
        c.close()
    if not row:
        raise MediaPlayerError("Playback device not found.",404)
    try:
        caps=json.loads(str(row["capabilities_json"]) or "{}")
    except json.JSONDecodeError:
        caps={}
    return {"contract":CONTRACT,"device":{
        "device_id":str(row["device_id"]),
        "name":str(row["name"]),
        "kind":str(row["kind"]),
        "capabilities":caps,
        "active":bool(row["active"]),
        "last_seen_at":str(row["last_seen_at"]),
    }}


def devices()->dict[str,Any]:
    c=_connect()
    try:
        rows=c.execute("SELECT device_id FROM playback_devices WHERE active=1 ORDER BY last_seen_at DESC,name").fetchall()
    finally:
        c.close()
    items=[device(str(row["device_id"]))["device"] for row in rows]
    return {"contract":CONTRACT,"devices":items,"count":len(items)}


def _delivery(item:dict[str,Any],caps:dict[str,Any])->tuple[str,str]:
    ext=str(item.get("extension") or "").lower()
    kind=str(item.get("media_type") or "")
    if not bool(caps.get("direct_play",True)):
        return "transcode","direct play disabled by device capability"
    if kind=="video" and ext in _DIRECT_VIDEO:
        return "direct","compatible video container"
    if kind=="audio" and ext in _DIRECT_AUDIO:
        return "direct","compatible audio container"
    if kind=="image":
        return "direct","image presentation"
    return "transcode","source format requires a compatible derivative"


def create_session(media_id:str,device_id:str,position_seconds:float|None=None,autoplay:bool=True)->dict[str,Any]:
    item=_item(media_id)
    dev=device(device_id)["device"]
    prior=dict(item.get("playback") or {})
    position=float(prior.get("position_seconds") or 0) if position_seconds is None else max(0.0,float(position_seconds))
    duration=max(0.0,float(prior.get("duration_seconds") or 0))
    delivery,reason=_delivery(item,dict(dev.get("capabilities") or {}))
    processor_job_id=""
    if delivery=="transcode":
        try:
            operation="video.convert" if item.get("media_type")=="video" else "audio.convert"
            output="mp4" if item.get("media_type")=="video" else "mp3"
            queued=homeserver_media_processor.enqueue(str(media_id),operation,"playback",output,50)
            processor_job_id=str(queued.get("job",{}).get("job_id") or queued.get("job_id") or "")
        except Exception as exc:
            raise MediaPlayerError(f"Playback transcode could not be queued: {exc}",422) from exc
    session_id="play_"+uuid.uuid4().hex
    state="playing" if autoplay else "paused"
    c=_connect()
    try:
        c.execute(
            """INSERT INTO playback_sessions(
                 session_id,media_id,device_id,state,delivery_mode,position_seconds,duration_seconds,processor_job_id
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (session_id,str(media_id),str(device_id),state,delivery,position,duration,processor_job_id),
        )
        c.execute(
            """INSERT INTO playback_history(history_id,media_id,session_id,event_type,position_seconds)
               VALUES (?,?,?,?,?)""",
            ("hist_"+uuid.uuid4().hex,str(media_id),session_id,"session.created",position),
        )
        c.commit()
    finally:
        c.close()
    result=session(session_id)
    result["delivery_reason"]=reason
    result["source_file_modified"]=False
    result["source_file_deleted"]=False
    return result


def _session_row(session_id:str)->sqlite3.Row:
    c=_connect()
    try:
        row=c.execute("SELECT * FROM playback_sessions WHERE session_id=?",(str(session_id),)).fetchone()
    finally:
        c.close()
    if not row:
        raise MediaPlayerError("Playback session not found.",404)
    return row


def session(session_id:str)->dict[str,Any]:
    row=_session_row(session_id)
    transcode=None
    processor_job_id=str(row["processor_job_id"] or "")
    if processor_job_id:
        try:
            transcode=homeserver_media_processor.job(processor_job_id)["job"]
        except Exception:
            transcode={"job_id":processor_job_id,"status":"unknown"}
    return {"contract":CONTRACT,"session":{
        "session_id":str(row["session_id"]),
        "media_id":str(row["media_id"]),
        "device_id":str(row["device_id"]),
        "state":str(row["state"]),
        "delivery_mode":str(row["delivery_mode"]),
        "position_seconds":float(row["position_seconds"]),
        "duration_seconds":float(row["duration_seconds"]),
        "processor_job_id":processor_job_id,
        "transcode":transcode,
        "error":str(row["error"] or ""),
        "updated_at":str(row["updated_at"]),
        "stream_path":f"/api/v1/control/homeserver-apps/media-server/stream/{row['media_id']}" if str(row["delivery_mode"])=="direct" else "",
        "filesystem_path_exposed":False,
    }}


def control(session_id:str,command:str,position_seconds:float|None=None,duration_seconds:float|None=None)->dict[str,Any]:
    row=_session_row(session_id)
    command=str(command or "").lower()
    if command not in {"play","pause","stop","seek","complete"}:
        raise MediaPlayerError("Unsupported playback command.")
    position=float(row["position_seconds"])
    duration=float(row["duration_seconds"])
    if position_seconds is not None:
        position=max(0.0,float(position_seconds))
    if duration_seconds is not None:
        duration=max(0.0,float(duration_seconds))
    state={"play":"playing","pause":"paused","stop":"stopped","seek":str(row["state"]),"complete":"completed"}[command]
    completed=command=="complete" or (duration>0 and position>=max(0,duration-2))
    if completed:
        state="completed"
    try:
        homeserver_media_server.update_playback(str(row["media_id"]),position,duration,completed)
    except homeserver_media_server.MediaServerError as exc:
        raise MediaPlayerError(str(exc),exc.status_code) from exc
    c=_connect()
    try:
        c.execute(
            """UPDATE playback_sessions SET state=?,position_seconds=?,duration_seconds=?,updated_at=CURRENT_TIMESTAMP
               WHERE session_id=?""",
            (state,position,duration,str(session_id)),
        )
        c.execute(
            """INSERT INTO playback_history(history_id,media_id,session_id,event_type,position_seconds,completed)
               VALUES (?,?,?,?,?,?)""",
            ("hist_"+uuid.uuid4().hex,str(row["media_id"]),str(session_id),"playback."+command,position,1 if completed else 0),
        )
        c.commit()
    finally:
        c.close()
    return session(session_id)


def continue_watching(limit:int=24)->dict[str,Any]:
    limit=max(1,min(int(limit),100))
    c=_connect()
    try:
        rows=c.execute(
            """SELECT ps.media_id,ps.updated_at
               FROM playback_sessions ps
               WHERE ps.rowid IN (
                 SELECT MAX(rowid) FROM playback_sessions GROUP BY media_id
               )
                 AND ps.position_seconds>0
                 AND ps.state!='completed'
               ORDER BY ps.updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        c.close()
    items=[]
    for row in rows:
        try:
            item=_item(str(row["media_id"]))
        except MediaPlayerError:
            continue
        playback=dict(item.get("playback") or {})
        items.append({
            "media_id":str(row["media_id"]),
            "title":str(item.get("title") or item.get("name") or ""),
            "media_type":str(item.get("media_type") or ""),
            "position_seconds":float(playback.get("position_seconds") or 0),
            "duration_seconds":float(playback.get("duration_seconds") or 0),
            "updated_at":str(row["updated_at"]),
        })
    return {"contract":CONTRACT,"items":items,"count":len(items)}


def recent(limit:int=50)->dict[str,Any]:
    limit=max(1,min(int(limit),200))
    c=_connect()
    try:
        rows=c.execute(
            """SELECT * FROM playback_history ORDER BY created_at DESC LIMIT ?""",(limit,)
        ).fetchall()
    finally:
        c.close()
    return {"contract":CONTRACT,"events":[{
        "media_id":str(r["media_id"]),
        "session_id":str(r["session_id"]),
        "event_type":str(r["event_type"]),
        "position_seconds":float(r["position_seconds"]),
        "completed":bool(r["completed"]),
        "created_at":str(r["created_at"]),
    } for r in rows],"count":len(rows)}


def handoff(session_id:str,device_id:str)->dict[str,Any]:
    row=_session_row(session_id)
    device(device_id)
    replacement=create_session(str(row["media_id"]),str(device_id),float(row["position_seconds"]),str(row["state"])=="playing")
    c=_connect()
    try:
        c.execute("UPDATE playback_sessions SET state='handed_off',updated_at=CURRENT_TIMESTAMP WHERE session_id=?",(str(session_id),))
        c.commit()
    finally:
        c.close()
    replacement["handed_off_from"]=str(session_id)
    return replacement


def status()->dict[str,Any]:
    c=_connect()
    try:
        active=int(c.execute("SELECT COUNT(*) FROM playback_sessions WHERE state IN ('playing','paused')").fetchone()[0])
        transcoding=int(c.execute("SELECT COUNT(*) FROM playback_sessions WHERE delivery_mode='transcode' AND state IN ('playing','paused')").fetchone()[0])
        device_count=int(c.execute("SELECT COUNT(*) FROM playback_devices WHERE active=1").fetchone()[0])
    finally:
        c.close()
    return {
        "contract":CONTRACT,
        "app_key":APP_KEY,
        "active_sessions":active,
        "transcoding_sessions":transcoding,
        "active_devices":device_count,
        "homeserver_execution_authority":True,
        "source_files_modified":False,
        "source_files_deleted":False,
    }


def brain_context(limit:int=8)->dict[str,Any]:
    return {
        "contract":"vp3.media-player.brain-context.v1",
        "status":status(),
        "continue_watching":continue_watching(limit)["items"],
        "recent":recent(limit)["events"],
        "governance":{
            "home_server_is_execution_authority":True,
            "source_files_never_modified_or_deleted":True,
            "playback_control_is_non_destructive":True,
            "transcodes_use_media_processor":True,
        },
        "filesystem_paths_exposed":False,
    }


def invoke(action:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    a=dict(arguments or {})
    key=str(action or "")
    if key=="player.status": return status()
    if key=="player.devices": return devices()
    if key=="player.device.register": return register_device(str(a.get("name") or "Playback Device"),str(a.get("kind") or "browser"),dict(a.get("capabilities") or {}),str(a.get("device_id") or ""))
    if key=="player.session.get": return session(str(a.get("session_id") or ""))
    if key=="player.play":
        return create_session(str(a.get("media_id") or ""),str(a.get("device_id") or ""),a.get("position_seconds"),True)
    if key=="player.pause": return control(str(a.get("session_id") or ""),"pause",a.get("position_seconds"),a.get("duration_seconds"))
    if key=="player.resume": return control(str(a.get("session_id") or ""),"play",a.get("position_seconds"),a.get("duration_seconds"))
    if key=="player.seek": return control(str(a.get("session_id") or ""),"seek",a.get("position_seconds"),a.get("duration_seconds"))
    if key=="player.stop": return control(str(a.get("session_id") or ""),"stop",a.get("position_seconds"),a.get("duration_seconds"))
    if key=="player.complete": return control(str(a.get("session_id") or ""),"complete",a.get("position_seconds"),a.get("duration_seconds"))
    if key=="player.handoff": return handoff(str(a.get("session_id") or ""),str(a.get("device_id") or ""))
    if key=="player.continue-watching": return continue_watching(int(a.get("limit",24)))
    if key=="player.recent": return recent(int(a.get("limit",50)))
    if key=="player.brain-context": return brain_context(int(a.get("limit",8)))
    raise MediaPlayerError("Unsupported Media Player action.",404)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "app_key":APP_KEY,
        "direct_play":True,
        "media_processor_transcode_fallback":True,
        "resume_positions":True,
        "watched_state":True,
        "playback_history":True,
        "continue_watching":True,
        "device_registry":True,
        "cross_device_handoff":True,
        "agent_control":True,
        "agent_brain_context":True,
        "filesystem_paths_exposed":False,
        "source_file_writes":False,
        "source_file_deletes":False,
        "homeserver_execution_authority":True,
    }

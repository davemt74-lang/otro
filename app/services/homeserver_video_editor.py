from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from . import homeserver_app_resources, homeserver_apps, homeserver_media_server

APP_KEY = "vp3.video-editor"
CONTRACT = "vp3.video-editor.v1"
MAX_TRACKS = 32
MAX_CLIPS = 5000


class VideoEditorError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _ensure_app() -> dict[str, Any]:
    try:
        app = homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise VideoEditorError("VP3 Video Editor is not installed.", 404) from exc
    if not app.get("installed_version"):
        raise VideoEditorError("VP3 Video Editor is not installed.", 409)
    return app


def _connect() -> sqlite3.Connection:
    _ensure_app()
    path = homeserver_app_resources.sqlite_path(APP_KEY, "video-editor.db")
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS video_projects(
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            width INTEGER NOT NULL DEFAULT 1920,
            height INTEGER NOT NULL DEFAULT 1080,
            fps REAL NOT NULL DEFAULT 30,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS video_tracks(
            track_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            position INTEGER NOT NULL,
            muted INTEGER NOT NULL DEFAULT 0 CHECK(muted IN (0,1)),
            locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
            FOREIGN KEY(project_id) REFERENCES video_projects(project_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS video_clips(
            clip_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            media_id TEXT NOT NULL,
            start_seconds REAL NOT NULL DEFAULT 0,
            in_seconds REAL NOT NULL DEFAULT 0,
            out_seconds REAL NOT NULL DEFAULT 0,
            volume REAL NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES video_projects(project_id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES video_tracks(track_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_video_tracks_project ON video_tracks(project_id,position);
        CREATE INDEX IF NOT EXISTS idx_video_clips_project ON video_clips(project_id,start_seconds);
        CREATE TABLE IF NOT EXISTS video_render_jobs(
            render_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            format TEXT NOT NULL DEFAULT 'mp4',
            preset TEXT NOT NULL DEFAULT '1080p',
            progress REAL NOT NULL DEFAULT 0,
            output_name TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES video_projects(project_id) ON DELETE CASCADE
        );
        """
    )
    return connection


def _project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": str(row["project_id"]),
        "name": str(row["name"]),
        "width": int(row["width"]),
        "height": int(row["height"]),
        "fps": float(row["fps"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_projects(limit: int = 100) -> dict[str, Any]:
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT * FROM video_projects ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        ).fetchall()
    finally:
        connection.close()
    return {"contract": CONTRACT, "projects": [_project(row) for row in rows], "count": len(rows)}


def create_project(name: str, width: int = 1920, height: int = 1080, fps: float = 30) -> dict[str, Any]:
    label = " ".join(str(name or "").split())[:160]
    if not label:
        raise VideoEditorError("Project name is required.")
    width = max(320, min(7680, int(width)))
    height = max(240, min(4320, int(height)))
    fps = max(1.0, min(240.0, float(fps)))
    pid = "ved_" + uuid.uuid4().hex
    video_track = "trk_" + uuid.uuid4().hex
    audio_track = "trk_" + uuid.uuid4().hex
    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO video_projects(project_id,name,width,height,fps) VALUES (?,?,?,?,?)",
            (pid, label, width, height, fps),
        )
        connection.execute(
            "INSERT INTO video_tracks(track_id,project_id,kind,name,position) VALUES (?,?, 'video','Video 1',0)",
            (video_track, pid),
        )
        connection.execute(
            "INSERT INTO video_tracks(track_id,project_id,kind,name,position) VALUES (?,?, 'audio','Audio 1',1)",
            (audio_track, pid),
        )
        connection.commit()
    finally:
        connection.close()
    return project(pid)


def project(project_id: str) -> dict[str, Any]:
    pid = str(project_id or "").strip()
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM video_projects WHERE project_id=?", (pid,)).fetchone()
        if not row:
            raise VideoEditorError("Video Editor project not found.", 404)
        tracks = [dict(r) for r in connection.execute(
            "SELECT * FROM video_tracks WHERE project_id=? ORDER BY position,track_id", (pid,)
        ).fetchall()]
        clips = []
        for r in connection.execute(
            "SELECT * FROM video_clips WHERE project_id=? ORDER BY start_seconds,clip_id", (pid,)
        ).fetchall():
            item = dict(r)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            clips.append(item)
        renders = [dict(r) for r in connection.execute(
            "SELECT * FROM video_render_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 20", (pid,)
        ).fetchall()]
    finally:
        connection.close()
    return {"contract": CONTRACT, "project": _project(row), "tracks": tracks, "clips": clips, "renders": renders}


def add_track(project_id: str, kind: str, name: str = "") -> dict[str, Any]:
    pid = str(project_id or "").strip()
    kind = str(kind or "").strip().lower()
    if kind not in {"video", "audio"}:
        raise VideoEditorError("Track kind must be video or audio.")
    connection = _connect()
    try:
        if not connection.execute("SELECT 1 FROM video_projects WHERE project_id=?", (pid,)).fetchone():
            raise VideoEditorError("Video Editor project not found.", 404)
        count = int(connection.execute("SELECT COUNT(*) FROM video_tracks WHERE project_id=?", (pid,)).fetchone()[0])
        if count >= MAX_TRACKS:
            raise VideoEditorError("Project track limit reached.", 409)
        track_id = "trk_" + uuid.uuid4().hex
        label = " ".join(str(name or "").split())[:120] or f"{kind.title()} {count + 1}"
        connection.execute(
            "INSERT INTO video_tracks(track_id,project_id,kind,name,position) VALUES (?,?,?,?,?)",
            (track_id, pid, kind, label, count),
        )
        connection.execute("UPDATE video_projects SET updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
        connection.commit()
    finally:
        connection.close()
    return project(pid)


def add_clip(project_id: str, track_id: str, media_id: str, start_seconds: float = 0) -> dict[str, Any]:
    pid = str(project_id or "").strip()
    tid = str(track_id or "").strip()
    mid = str(media_id or "").strip()
    if not mid:
        raise VideoEditorError("media_id is required.")
    try:
        source = homeserver_media_server.item(mid)["item"]
    except Exception as exc:
        raise VideoEditorError("Media Server item is unavailable.", 409) from exc
    connection = _connect()
    try:
        track = connection.execute(
            "SELECT * FROM video_tracks WHERE track_id=? AND project_id=?", (tid, pid)
        ).fetchone()
        if not track:
            raise VideoEditorError("Timeline track not found.", 404)
        if int(connection.execute("SELECT COUNT(*) FROM video_clips WHERE project_id=?", (pid,)).fetchone()[0]) >= MAX_CLIPS:
            raise VideoEditorError("Project clip limit reached.", 409)
        media_type = str(source.get("media_type") or "")
        if track["kind"] == "audio" and media_type not in {"audio", "video"}:
            raise VideoEditorError("Only audio or video media can be placed on an audio track.", 409)
        if track["kind"] == "video" and media_type not in {"video", "image"}:
            raise VideoEditorError("Only video or image media can be placed on a video track.", 409)
        cid = "clip_" + uuid.uuid4().hex
        duration = float((source.get("playback") or {}).get("duration_seconds") or 0)
        connection.execute(
            """INSERT INTO video_clips(
                clip_id,project_id,track_id,media_id,start_seconds,in_seconds,out_seconds,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                cid, pid, tid, mid, max(0.0, float(start_seconds)), 0.0, max(0.0, duration),
                json.dumps({"title": source.get("title", ""), "media_type": media_type}, separators=(",", ":")),
            ),
        )
        connection.execute("UPDATE video_projects SET updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
        connection.commit()
    finally:
        connection.close()
    return project(pid)


def update_clip(
    project_id: str,
    clip_id: str,
    *,
    start_seconds: float | None = None,
    in_seconds: float | None = None,
    out_seconds: float | None = None,
    volume: float | None = None,
) -> dict[str, Any]:
    pid = str(project_id or "").strip()
    cid = str(clip_id or "").strip()
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM video_clips WHERE clip_id=? AND project_id=?", (cid, pid)
        ).fetchone()
        if not row:
            raise VideoEditorError("Timeline clip not found.", 404)
        start = max(0.0, float(row["start_seconds"] if start_seconds is None else start_seconds))
        inp = max(0.0, float(row["in_seconds"] if in_seconds is None else in_seconds))
        out = max(0.0, float(row["out_seconds"] if out_seconds is None else out_seconds))
        if out and out < inp:
            raise VideoEditorError("Clip out point cannot be before the in point.")
        vol = max(0.0, min(4.0, float(row["volume"] if volume is None else volume)))
        connection.execute(
            """UPDATE video_clips SET start_seconds=?,in_seconds=?,out_seconds=?,volume=?,
               updated_at=CURRENT_TIMESTAMP WHERE clip_id=?""",
            (start, inp, out, vol, cid),
        )
        connection.execute("UPDATE video_projects SET updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
        connection.commit()
    finally:
        connection.close()
    return project(pid)


def remove_clip(project_id: str, clip_id: str) -> dict[str, Any]:
    pid = str(project_id or "").strip()
    cid = str(clip_id or "").strip()
    connection = _connect()
    try:
        cur = connection.execute("DELETE FROM video_clips WHERE clip_id=? AND project_id=?", (cid, pid))
        if cur.rowcount < 1:
            raise VideoEditorError("Timeline clip not found.", 404)
        connection.execute("UPDATE video_projects SET updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
        connection.commit()
    finally:
        connection.close()
    return project(pid)


def queue_clip_proxy(project_id:str,clip_id:str)->dict[str,Any]:
    pid=str(project_id or "").strip()
    cid=str(clip_id or "").strip()
    connection=_connect()
    try:
        row=connection.execute(
            "SELECT media_id FROM video_clips WHERE clip_id=? AND project_id=?",(cid,pid)
        ).fetchone()
    finally:
        connection.close()
    if not row:
        raise VideoEditorError("Timeline clip not found.",404)
    try:
        from . import homeserver_media_processor
        result=homeserver_media_processor.enqueue(str(row["media_id"]),"proxy","editor","mp4",10)
    except Exception as exc:
        raise VideoEditorError(str(exc),getattr(exc,"status_code",422)) from exc
    return {
        "contract":CONTRACT,
        "project_id":pid,
        "clip_id":cid,
        "media_id":str(row["media_id"]),
        "processor_job":result["job"],
        "processor":"vp3.media-processor",
    }


def queue_render(project_id: str, preset: str = "1080p", format_name: str = "mp4") -> dict[str, Any]:
    pid = str(project_id or "").strip()
    preset = str(preset or "1080p").strip().lower()
    format_name = str(format_name or "mp4").strip().lower()
    if preset not in {"720p", "1080p", "4k", "source"}:
        raise VideoEditorError("Unsupported render preset.")
    if format_name not in {"mp4", "webm"}:
        raise VideoEditorError("Unsupported render format.")
    snapshot = project(pid)
    if not snapshot["clips"]:
        raise VideoEditorError("Add at least one clip before rendering.", 409)
    rid = "render_" + uuid.uuid4().hex
    connection = _connect()
    try:
        connection.execute(
            """INSERT INTO video_render_jobs(render_id,project_id,status,format,preset,output_name)
               VALUES (?,?, 'queued',?,?,?)""",
            (rid, pid, format_name, preset, f"{snapshot['project']['name']}.{format_name}"),
        )
        connection.commit()
    finally:
        connection.close()
    return {"contract": CONTRACT, "render": render_status(rid), "execution": "homeserver_local_worker"}


def render_status(render_id: str) -> dict[str, Any]:
    rid = str(render_id or "").strip()
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM video_render_jobs WHERE render_id=?", (rid,)).fetchone()
    finally:
        connection.close()
    if not row:
        raise VideoEditorError("Render job not found.", 404)
    return dict(row)


def status() -> dict[str, Any]:
    app = _ensure_app()
    connection = _connect()
    try:
        projects = int(connection.execute("SELECT COUNT(*) FROM video_projects").fetchone()[0])
        clips = int(connection.execute("SELECT COUNT(*) FROM video_clips").fetchone()[0])
        queued = int(connection.execute("SELECT COUNT(*) FROM video_render_jobs WHERE status IN ('queued','rendering')").fetchone()[0])
    finally:
        connection.close()
    media_available = True
    try:
        media = homeserver_media_server.status()
    except Exception:
        media_available = False
        media = {"library_count": 0}
    return {
        "contract": CONTRACT,
        "app_key": APP_KEY,
        "installed_version": app.get("installed_version"),
        "lifecycle_state": app.get("lifecycle_state"),
        "projects": projects,
        "clips": clips,
        "renders_active": queued,
        "media_server_available": media_available,
        "media_library_count": int(media.get("library_count") or 0),
        "source_media_owned_by_editor": False,
        "render_execution": "homeserver_local_worker",
    }


def agent_actions() -> dict[str, Any]:
    return {
        "contract": "vp3.app.agent-actions.v1",
        "app_key": APP_KEY,
        "complete_control": True,
        "actions": [
            {"key": "video.projects.list", "risk": "read", "requires_confirmation": False},
            {"key": "video.project.get", "risk": "read", "requires_confirmation": False},
            {"key": "video.project.create", "risk": "write", "requires_confirmation": False},
            {"key": "video.track.add", "risk": "write", "requires_confirmation": False},
            {"key": "video.clip.add", "risk": "write", "requires_confirmation": False},
            {"key": "video.clip.update", "risk": "write", "requires_confirmation": False},
            {"key": "video.clip.remove", "risk": "destructive", "requires_confirmation": True},
            {"key": "video.render.queue", "risk": "consequential", "requires_confirmation": True},
            {"key": "video.clip.proxy", "risk": "consequential", "requires_confirmation": True},
        ],
    }


def invoke(action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = dict(arguments or {})
    key = str(action or "").strip()
    if key == "video.projects.list":
        return list_projects(int(args.get("limit", 100)))
    if key == "video.project.get":
        return project(str(args.get("project_id") or ""))
    if key == "video.project.create":
        return create_project(
            str(args.get("name") or ""),
            int(args.get("width", 1920)),
            int(args.get("height", 1080)),
            float(args.get("fps", 30)),
        )
    if key == "video.track.add":
        return add_track(str(args.get("project_id") or ""), str(args.get("kind") or ""), str(args.get("name") or ""))
    if key == "video.clip.add":
        return add_clip(
            str(args.get("project_id") or ""), str(args.get("track_id") or ""),
            str(args.get("media_id") or ""), float(args.get("start_seconds", 0)),
        )
    if key == "video.clip.update":
        return update_clip(
            str(args.get("project_id") or ""), str(args.get("clip_id") or ""),
            start_seconds=args.get("start_seconds"), in_seconds=args.get("in_seconds"),
            out_seconds=args.get("out_seconds"), volume=args.get("volume"),
        )
    if key == "video.clip.remove":
        return remove_clip(str(args.get("project_id") or ""), str(args.get("clip_id") or ""))
    if key == "video.render.queue":
        return queue_render(
            str(args.get("project_id") or ""), str(args.get("preset") or "1080p"), str(args.get("format") or "mp4")
        )
    if key == "video.clip.proxy":
        return queue_clip_proxy(str(args.get("project_id") or ""),str(args.get("clip_id") or ""))
    raise VideoEditorError("Unsupported Video Editor agent action.", 404)


def public_capability() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "projects": True,
        "multi_track_timeline": True,
        "media_server_source_library": True,
        "non_destructive_source_media": True,
        "local_render_queue": True,
        "media_processor_proxy_handoff": True,
        "agent_complete_control": True,
        "agent_actions": agent_actions()["actions"],
        "cloud_projects_projection": True,
        "cloud_render_status_projection": True,
    }

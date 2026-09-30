from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import PurePosixPath
from typing import Any

from . import homeserver_app_resources, homeserver_apps, homeserver_media_server

APP_KEY="vp3.music-server"
CONTRACT="vp3.music-server.v1"


class MusicServerError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _ensure_app()->dict[str,Any]:
    try:
        app=homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise MusicServerError("VP3 Music Server is not installed.",404) from exc
    if not app.get("installed_version"):
        raise MusicServerError("VP3 Music Server is not installed.",409)
    return app


def _connect()->sqlite3.Connection:
    _ensure_app()
    path=homeserver_app_resources.sqlite_path(APP_KEY,"music-server.db")
    connection=sqlite3.connect(path,timeout=15)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS music_tracks(
            media_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT NOT NULL,
            track_no INTEGER NOT NULL DEFAULT 0,
            root_id TEXT NOT NULL,
            source_updated_at TEXT NOT NULL DEFAULT '',
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_music_track_artist_album ON music_tracks(artist,album,title);
        CREATE TABLE IF NOT EXISTS music_favorites(
            media_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS music_playlists(
            playlist_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS music_playlist_items(
            playlist_id TEXT NOT NULL,
            media_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(playlist_id,media_id),
            FOREIGN KEY(playlist_id) REFERENCES music_playlists(playlist_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_music_playlist_position ON music_playlist_items(playlist_id,position);
        CREATE TABLE IF NOT EXISTS music_queue(
            position INTEGER PRIMARY KEY,
            media_id TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS music_state(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            current_media_id TEXT NOT NULL DEFAULT '',
            playing INTEGER NOT NULL DEFAULT 0 CHECK(playing IN (0,1)),
            position_seconds REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO music_state(singleton) VALUES (1);
        """
    )
    return connection


def _metadata(relative_path:str,title:str)->tuple[str,str,str,int]:
    parts=list(PurePosixPath(str(relative_path or "")).parts)
    stem=str(title or "").strip() or "Unknown Track"
    artist="Unknown Artist"
    album="Unknown Album"
    if len(parts)>=3:
        artist=parts[-3].strip() or artist
        album=parts[-2].strip() or album
    elif len(parts)==2:
        album=parts[-2].strip() or album
    track_no=0
    candidate=stem
    prefix=candidate.split(" ",1)[0].rstrip(".-")
    if prefix.isdigit():
        track_no=int(prefix)
        candidate=candidate[len(prefix):].lstrip(" .-_")
    if " - " in candidate and artist=="Unknown Artist":
        bits=[x.strip() for x in candidate.split(" - ") if x.strip()]
        if len(bits)>=2:
            artist=bits[0]
            candidate=bits[-1]
    return candidate or stem,artist[:240],album[:240],track_no


def sync()->dict[str,Any]:
    _ensure_app()
    try:
        records=homeserver_media_server.audio_source_records()
    except Exception as exc:
        raise MusicServerError("Media Server is unavailable. Install, map a media folder, and scan it first.",409) from exc
    connection=_connect()
    try:
        seen=[]
        for row in records:
            media_id=str(row["media_id"])
            title,artist,album,track_no=_metadata(str(row.get("relative_path") or ""),str(row.get("title") or ""))
            connection.execute(
                """INSERT INTO music_tracks(media_id,title,artist,album,track_no,root_id,source_updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(media_id) DO UPDATE SET
                     title=excluded.title,artist=excluded.artist,album=excluded.album,track_no=excluded.track_no,
                     root_id=excluded.root_id,source_updated_at=excluded.source_updated_at,indexed_at=CURRENT_TIMESTAMP""",
                (media_id,title,artist,album,track_no,str(row.get("root_id") or ""),str(row.get("updated_at") or "")),
            )
            seen.append(media_id)
        if seen:
            placeholders=",".join("?" for _ in seen)
            connection.execute(f"DELETE FROM music_tracks WHERE media_id NOT IN ({placeholders})",tuple(seen))
        else:
            connection.execute("DELETE FROM music_tracks")
        connection.execute("DELETE FROM music_favorites WHERE media_id NOT IN (SELECT media_id FROM music_tracks)")
        connection.execute("DELETE FROM music_playlist_items WHERE media_id NOT IN (SELECT media_id FROM music_tracks)")
        connection.execute("DELETE FROM music_queue WHERE media_id NOT IN (SELECT media_id FROM music_tracks)")
        connection.commit()
        total=int(connection.execute("SELECT COUNT(*) FROM music_tracks").fetchone()[0])
        artists=int(connection.execute("SELECT COUNT(DISTINCT artist) FROM music_tracks").fetchone()[0])
        albums=int(connection.execute("SELECT COUNT(DISTINCT artist||'\n'||album) FROM music_tracks").fetchone()[0])
    finally:
        connection.close()
    return {"contract":CONTRACT,"tracks":total,"artists":artists,"albums":albums,"source":"vp3.media-server"}


def _track(row:sqlite3.Row|dict[str,Any])->dict[str,Any]:
    return {
        "media_id":str(row["media_id"]),
        "title":str(row["title"]),
        "artist":str(row["artist"]),
        "album":str(row["album"]),
        "track_no":int(row["track_no"]),
        "stream_url":f"/api/v1/control/homeserver-apps/media-server/stream/{row['media_id']}",
        "source_owned_by_music_server":False,
    }


def tracks(query:str="",artist:str="",album:str="",limit:int=200)->dict[str,Any]:
    connection=_connect()
    clauses=[];params=[]
    q=" ".join(str(query or "").split())[:200]
    if q:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(artist) LIKE ? OR LOWER(album) LIKE ?)")
        term="%"+q.lower().replace("%","\\%").replace("_","\\_")+"%"
        params.extend([term,term,term])
    if artist:
        clauses.append("artist=?");params.append(str(artist)[:240])
    if album:
        clauses.append("album=?");params.append(str(album)[:240])
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    try:
        rows=connection.execute(
            "SELECT * FROM music_tracks"+where+" ORDER BY artist COLLATE NOCASE,album COLLATE NOCASE,track_no,title COLLATE NOCASE LIMIT ?",
            (*params,max(1,min(int(limit),1000))),
        ).fetchall()
        total=int(connection.execute("SELECT COUNT(*) FROM music_tracks"+where,tuple(params)).fetchone()[0])
    finally:
        connection.close()
    return {"contract":CONTRACT,"tracks":[_track(row) for row in rows],"count":len(rows),"total":total}


def artists(limit:int=500)->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT artist,COUNT(*) tracks,COUNT(DISTINCT album) albums
               FROM music_tracks GROUP BY artist ORDER BY artist COLLATE NOCASE LIMIT ?""",
            (max(1,min(int(limit),1000)),),
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"artists":[dict(row) for row in rows],"count":len(rows)}


def albums(artist:str="",limit:int=500)->dict[str,Any]:
    connection=_connect()
    try:
        if artist:
            rows=connection.execute(
                """SELECT artist,album,COUNT(*) tracks FROM music_tracks WHERE artist=?
                   GROUP BY artist,album ORDER BY album COLLATE NOCASE LIMIT ?""",
                (str(artist)[:240],max(1,min(int(limit),1000))),
            ).fetchall()
        else:
            rows=connection.execute(
                """SELECT artist,album,COUNT(*) tracks FROM music_tracks
                   GROUP BY artist,album ORDER BY artist COLLATE NOCASE,album COLLATE NOCASE LIMIT ?""",
                (max(1,min(int(limit),1000)),),
            ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"albums":[dict(row) for row in rows],"count":len(rows)}


def favorite(media_id:str,enabled:bool=True)->dict[str,Any]:
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM music_tracks WHERE media_id=?",(media_id,)).fetchone():
            raise MusicServerError("Track not found.",404)
        if enabled:
            connection.execute("INSERT OR IGNORE INTO music_favorites(media_id) VALUES (?)",(media_id,))
        else:
            connection.execute("DELETE FROM music_favorites WHERE media_id=?",(media_id,))
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"media_id":media_id,"favorite":bool(enabled)}


def favorites()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT t.* FROM music_favorites f JOIN music_tracks t ON t.media_id=f.media_id
               ORDER BY f.created_at DESC"""
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"tracks":[_track(row) for row in rows],"count":len(rows)}


def create_playlist(name:str)->dict[str,Any]:
    label=" ".join(str(name or "").split())[:160]
    if not label:
        raise MusicServerError("Playlist name is required.")
    playlist_id="pl_"+uuid.uuid4().hex
    connection=_connect()
    try:
        connection.execute("INSERT INTO music_playlists(playlist_id,name) VALUES (?,?)",(playlist_id,label))
        connection.commit()
    finally:
        connection.close()
    return playlist(playlist_id)


def playlists()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT p.*,COUNT(i.media_id) tracks FROM music_playlists p
               LEFT JOIN music_playlist_items i ON i.playlist_id=p.playlist_id
               GROUP BY p.playlist_id ORDER BY p.updated_at DESC"""
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"playlists":[dict(row) for row in rows],"count":len(rows)}


def playlist(playlist_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        row=connection.execute("SELECT * FROM music_playlists WHERE playlist_id=?",(playlist_id,)).fetchone()
        if not row:
            raise MusicServerError("Playlist not found.",404)
        items=connection.execute(
            """SELECT t.* FROM music_playlist_items i JOIN music_tracks t ON t.media_id=i.media_id
               WHERE i.playlist_id=? ORDER BY i.position,t.title""",
            (playlist_id,),
        ).fetchall()
    finally:
        connection.close()
    return {"contract":CONTRACT,"playlist":dict(row),"tracks":[_track(x) for x in items]}


def playlist_add(playlist_id:str,media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM music_playlists WHERE playlist_id=?",(playlist_id,)).fetchone():
            raise MusicServerError("Playlist not found.",404)
        if not connection.execute("SELECT 1 FROM music_tracks WHERE media_id=?",(media_id,)).fetchone():
            raise MusicServerError("Track not found.",404)
        pos=int(connection.execute("SELECT COALESCE(MAX(position),-1)+1 FROM music_playlist_items WHERE playlist_id=?",(playlist_id,)).fetchone()[0])
        connection.execute(
            """INSERT INTO music_playlist_items(playlist_id,media_id,position) VALUES (?,?,?)
               ON CONFLICT(playlist_id,media_id) DO UPDATE SET position=excluded.position""",
            (playlist_id,media_id,pos),
        )
        connection.execute("UPDATE music_playlists SET updated_at=CURRENT_TIMESTAMP WHERE playlist_id=?",(playlist_id,))
        connection.commit()
    finally:
        connection.close()
    return playlist(playlist_id)


def playlist_remove(playlist_id:str,media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        connection.execute("DELETE FROM music_playlist_items WHERE playlist_id=? AND media_id=?",(playlist_id,media_id))
        connection.execute("UPDATE music_playlists SET updated_at=CURRENT_TIMESTAMP WHERE playlist_id=?",(playlist_id,))
        connection.commit()
    finally:
        connection.close()
    return playlist(playlist_id)


def delete_playlist(playlist_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        cur=connection.execute("DELETE FROM music_playlists WHERE playlist_id=?",(playlist_id,))
        if cur.rowcount<1:
            raise MusicServerError("Playlist not found.",404)
        connection.commit()
    finally:
        connection.close()
    return {"contract":CONTRACT,"deleted":True,"playlist_id":playlist_id,"source_files_deleted":False}


def queue_add(media_id:str)->dict[str,Any]:
    connection=_connect()
    try:
        if not connection.execute("SELECT 1 FROM music_tracks WHERE media_id=?",(media_id,)).fetchone():
            raise MusicServerError("Track not found.",404)
        pos=int(connection.execute("SELECT COALESCE(MAX(position),-1)+1 FROM music_queue").fetchone()[0])
        connection.execute("INSERT INTO music_queue(position,media_id) VALUES (?,?)",(pos,media_id))
        connection.commit()
    finally:
        connection.close()
    return queue()


def queue_clear()->dict[str,Any]:
    connection=_connect()
    try:
        connection.execute("DELETE FROM music_queue")
        connection.commit()
    finally:
        connection.close()
    return queue()


def queue()->dict[str,Any]:
    connection=_connect()
    try:
        rows=connection.execute(
            """SELECT q.position,t.* FROM music_queue q JOIN music_tracks t ON t.media_id=q.media_id
               ORDER BY q.position"""
        ).fetchall()
        state=connection.execute("SELECT * FROM music_state WHERE singleton=1").fetchone()
    finally:
        connection.close()
    return {
        "contract":CONTRACT,
        "queue":[{"position":int(row["position"]),**_track(row)} for row in rows],
        "playing":bool(state["playing"]) if state else False,
        "current_media_id":str(state["current_media_id"]) if state else "",
        "position_seconds":float(state["position_seconds"]) if state else 0.0,
    }


def playback(command:str,media_id:str="",position_seconds:float=0)->dict[str,Any]:
    cmd=str(command or "").strip().lower()
    if cmd not in {"play","pause","stop"}:
        raise MusicServerError("Unsupported playback command.")
    connection=_connect()
    try:
        current=connection.execute("SELECT * FROM music_state WHERE singleton=1").fetchone()
        selected=media_id or (str(current["current_media_id"]) if current else "")
        if selected and not connection.execute("SELECT 1 FROM music_tracks WHERE media_id=?",(selected,)).fetchone():
            raise MusicServerError("Track not found.",404)
        playing=1 if cmd=="play" else 0
        if cmd=="stop":
            position_seconds=0
        connection.execute(
            """UPDATE music_state SET current_media_id=?,playing=?,position_seconds=?,updated_at=CURRENT_TIMESTAMP
               WHERE singleton=1""",
            (selected,playing,max(0.0,float(position_seconds))),
        )
        connection.commit()
    finally:
        connection.close()
    return queue()


def status()->dict[str,Any]:
    connection=_connect()
    try:
        total=int(connection.execute("SELECT COUNT(*) FROM music_tracks").fetchone()[0])
        artists_count=int(connection.execute("SELECT COUNT(DISTINCT artist) FROM music_tracks").fetchone()[0])
        albums_count=int(connection.execute("SELECT COUNT(DISTINCT artist||'\n'||album) FROM music_tracks").fetchone()[0])
        playlists_count=int(connection.execute("SELECT COUNT(*) FROM music_playlists").fetchone()[0])
        favorites_count=int(connection.execute("SELECT COUNT(*) FROM music_favorites").fetchone()[0])
    finally:
        connection.close()
    try:
        roots=homeserver_media_server.roots()
    except Exception:
        roots={"count":0,"mapped_sources":0}
    return {
        "contract":CONTRACT,
        "app_key":APP_KEY,
        "tracks":total,
        "artists":artists_count,
        "albums":albums_count,
        "playlists":playlists_count,
        "favorites":favorites_count,
        "media_roots":int(roots.get("count") or 0),
        "mapped_sources":int(roots.get("mapped_sources") or 0),
        "source":"vp3.media-server",
        "source_media_owned_by_music_server":False,
    }


def invoke(action:str,arguments:dict[str,Any]|None=None)->dict[str,Any]:
    args=dict(arguments or {})
    key=str(action or "").strip()
    if key=="music.status": return status()
    if key=="music.sync": return sync()
    if key=="music.search": return tracks(str(args.get("query") or ""),limit=int(args.get("limit",200)))
    if key=="music.artists": return artists(int(args.get("limit",500)))
    if key=="music.albums": return albums(str(args.get("artist") or ""),int(args.get("limit",500)))
    if key=="music.favorites": return favorites()
    if key=="music.favorite.set": return favorite(str(args.get("media_id") or ""),bool(args.get("enabled",True)))
    if key=="music.playlists": return playlists()
    if key=="music.playlist.create": return create_playlist(str(args.get("name") or ""))
    if key=="music.playlist.add": return playlist_add(str(args.get("playlist_id") or ""),str(args.get("media_id") or ""))
    if key=="music.playlist.remove": return playlist_remove(str(args.get("playlist_id") or ""),str(args.get("media_id") or ""))
    if key=="music.playlist.delete": return delete_playlist(str(args.get("playlist_id") or ""))
    if key=="music.queue": return queue()
    if key=="music.queue.add": return queue_add(str(args.get("media_id") or ""))
    if key=="music.queue.clear": return queue_clear()
    if key=="music.playback": return playback(str(args.get("command") or ""),str(args.get("media_id") or ""),float(args.get("position_seconds",0)))
    raise MusicServerError("Unsupported Music Server action.",404)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "media_server_source":True,
        "mapped_computer_folders":True,
        "artists_albums_tracks":True,
        "playlists":True,
        "favorites":True,
        "queue":True,
        "playback_control":True,
        "source_media_owned_by_music_server":False,
        "universal_agent_control":True,
    }

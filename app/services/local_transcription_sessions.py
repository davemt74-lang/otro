"""Owner-local Cloud-compatible persistent transcription, separate from Agent Chat.

Cloud cannot read local sessions until the owner explicitly shares one completed
session. Raw audio and recordings never cross the transcript relay.
"""
from __future__ import annotations
from datetime import datetime, timezone
import re
import secrets
from typing import Any
from ..database import db

CONTRACT="vp3.homeserver.transcription-session.v1"
_ID=re.compile(r"^[0-9a-f]{32}$")
MAX_SEGMENTS=300
MAX_TEXT=8000
MAX_SESSION_CHARS=120000

class TranscriptError(ValueError):
    def __init__(self, message:str, status_code:int=422):
        super().__init__(message);self.status_code=status_code

def _utc()->str:
    return datetime.now(timezone.utc).isoformat()

def _id(value:Any)->str:
    if not isinstance(value,str) or not _ID.fullmatch(value):
        raise TranscriptError("Invalid transcription session ID.",404)
    return value

def _get(connection,session_id:str):
    row=connection.execute(
        "SELECT * FROM local_transcription_sessions WHERE id=?",(session_id,)
    ).fetchone()
    if row is None:raise TranscriptError("Transcription session unavailable.",404)
    return row

def _payload(connection,row,with_segments:bool=False)->dict[str,Any]:
    result={
        "id":row["id"],"title":row["title"],"status":row["status"],
        "cloud_shared":bool(row["cloud_share"]),"started_at":row["started_at"],
        "ended_at":row["ended_at"],"segment_count":int(row["segment_count"]),
        "source":"homeserver_local_transcription",
        "speaker_attribution":"unidentified_single_channel",
        "speaker_identity_verified":False,
        "diarization_available":False,
        "timeline_ms":int(connection.execute("SELECT COALESCE(MAX(started_ms),0) FROM local_transcription_segments WHERE session_id=?",(row["id"],)).fetchone()[0]),
    }
    if with_segments:
        segments=connection.execute(
            "SELECT id,client_key,text,started_ms,created_at FROM local_transcription_segments "
            "WHERE session_id=? ORDER BY rowid",(row["id"],)
        ).fetchall()
        result["segments"]=[
            {"id":s["id"],"client_key":s["client_key"],"text":s["text"],
             "started_ms":s["started_ms"],"created_at":s["created_at"],"speaker":"Speaker 1","segment_index":index,
             "speaker_attribution":"unidentified_single_channel","speaker_identity_verified":False}
            for index,s in enumerate(segments)
        ]
    return result

def start(title:str="Untitled transcription")->dict[str,Any]:
    title=str(title or "").strip()[:190] or "Untitled transcription"
    sid=secrets.token_hex(16)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Only one local active transcription: avoid accidental parallel listening.
        active=conn.execute(
            "SELECT id FROM local_transcription_sessions WHERE status='active' LIMIT 1"
        ).fetchone()
        if active is not None:
            raise TranscriptError("Stop the active transcription before creating another.",409)
        conn.execute(
            "INSERT INTO local_transcription_sessions(id,title,status,cloud_share,started_at)"
            " VALUES(?,?,'active',0,?)",(sid,title,_utc()),
        )
        return {"contract":CONTRACT,"session":_payload(conn,_get(conn,sid))}

def append(session_id:str,text:str,client_key:str,started_ms:int=0)->dict[str,Any]:
    sid=_id(session_id)
    text=str(text or "").strip()
    if not text or len(text)>MAX_TEXT:
        raise TranscriptError("Transcription segment must contain 1–8,000 characters.")
    if not isinstance(client_key,str) or not _ID.fullmatch(client_key):
        raise TranscriptError("A unique segment key is required.")
    if type(started_ms) is not int or started_ms<0 or started_ms>24*60*60*1000:
        raise TranscriptError("Invalid transcription segment timing.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_get(conn,sid)
        existing=conn.execute(
            "SELECT id,text FROM local_transcription_segments WHERE session_id=? AND client_key=?",
            (sid,client_key),
        ).fetchone()
        if existing is not None:
            if existing["text"]!=text:
                raise TranscriptError("This segment key already belongs to different text.",409)
            return {"contract":CONTRACT,"duplicate":True,"session":_payload(conn,row)}
        if row["status"]!="active":
            raise TranscriptError("Transcription must be active before appending.",409)
        if int(row["segment_count"])>=MAX_SEGMENTS:
            raise TranscriptError("Transcription segment limit reached.",409)
        length=conn.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(text AS BLOB))),0) FROM local_transcription_segments "
            "WHERE session_id=?",(sid,),
        ).fetchone()[0]
        if int(length)+len(text.encode("utf-8"))>MAX_SESSION_CHARS:
            raise TranscriptError("Transcription document size limit reached.",409)
        previous=conn.execute("SELECT COALESCE(MAX(started_ms),0) FROM local_transcription_segments WHERE session_id=?",(sid,)).fetchone()[0]
        started_ms=max(started_ms,int(previous))
        conn.execute(
            "INSERT INTO local_transcription_segments"
            "(id,session_id,client_key,text,started_ms,created_at) VALUES(?,?,?,?,?,?)",
            (secrets.token_hex(16),sid,client_key,text,started_ms,_utc()),
        )
        conn.execute(
            "UPDATE local_transcription_sessions SET segment_count=segment_count+1 WHERE id=?",
            (sid,),
        )
        return {"contract":CONTRACT,"duplicate":False,"session":_payload(conn,_get(conn,sid))}

def stop(session_id:str)->dict[str,Any]:
    sid=_id(session_id)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_get(conn,sid)
        if row["status"]=="active":
            conn.execute(
                "UPDATE local_transcription_sessions SET status='completed',ended_at=? WHERE id=?",
                (_utc(),sid),
            )
        return {"contract":CONTRACT,"session":_payload(conn,_get(conn,sid),True)}

def get(session_id:str,*,paired:bool=False)->dict[str,Any]:
    sid=_id(session_id)
    with db() as conn:
        # Consent and segment rows are one SQLite snapshot. A concurrent revoke
        # cannot splice a different document state into an authorized response.
        conn.execute("BEGIN")
        row=_get(conn,sid)
        if paired and (not row["cloud_share"] or row["status"]!="completed"):
            raise TranscriptError("This transcription is not shared with Cloud.",403)
        return {"contract":CONTRACT,"session":_payload(conn,row,True),
                "raw_audio_included":False}

def list_sessions(*,paired:bool=False,limit:int=50)->dict[str,Any]:
    with db() as conn:
        conn.execute("BEGIN")
        if paired:
            rows=conn.execute(
                "SELECT * FROM local_transcription_sessions WHERE cloud_share=1 "
                "AND status='completed' ORDER BY started_at DESC LIMIT ?",
                (min(50,max(1,limit)),),
            ).fetchall()
        else:
            rows=conn.execute(
                "SELECT * FROM local_transcription_sessions ORDER BY started_at DESC LIMIT ?",
                (min(50,max(1,limit)),),
            ).fetchall()
        return {"contract":CONTRACT,"sessions":[_payload(conn,row) for row in rows],
                "raw_audio_included":False}

def share_with_cloud(session_id:str,allowed:bool)->dict[str,Any]:
    sid=_id(session_id)
    if type(allowed) is not bool:raise TranscriptError("Share setting must be explicit.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_get(conn,sid)
        if allowed and row["status"]!="completed":
            raise TranscriptError("Stop listening before sharing a transcription.",409)
        conn.execute(
            "UPDATE local_transcription_sessions SET cloud_share=? WHERE id=?",
            (int(allowed),sid),
        )
        return {"contract":CONTRACT,"session":_payload(conn,_get(conn,sid)),
                "cloud_shares_text_only":True}

def delete(session_id:str)->dict[str,Any]:
    sid=_id(session_id)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_get(conn,sid)
        if row["status"]=="active":
            raise TranscriptError("Stop listening before deleting a transcription.",409)
        conn.execute("DELETE FROM local_transcription_segments WHERE session_id=?",(sid,))
        conn.execute("DELETE FROM local_transcription_sessions WHERE id=?",(sid,))
    return {"contract":CONTRACT,"deleted":True,"session_id":sid}

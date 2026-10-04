"""Owner-local Cloud-compatible persistent transcription, separate from Agent Chat.

Cloud cannot read local sessions until the owner explicitly shares one completed
session. Raw audio and recordings never cross the transcript relay.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import re
import secrets
from typing import Any
from ..database import db

CONTRACT="vp3.homeserver.transcription-session.v1"
_ID=re.compile(r"^[0-9a-f]{32}$")
MAX_SEGMENTS=300
MAX_TEXT=8000
MAX_SESSION_CHARS=120000
_ATTRIBUTION_SOURCES={"unknown","provider_diarization","heuristic_acoustic"}

def _clean_label(value:Any)->str:
    label=" ".join(str(value or "").split())[:80]
    return label or "Speaker 1"

def _sanitize_attribution(value:Any,speaker_label:str="Speaker 1")->dict[str,Any]:
    source=value if isinstance(value,dict) else {}
    source_name=str(source.get("source") or "unknown").strip().lower()
    if source.get("contract") not in (None,"","speaker-attribution-v1-20261004") or source_name not in _ATTRIBUTION_SOURCES:
        raise TranscriptError("Unsupported transcription speaker attribution.")
    label=_clean_label(source.get("speaker_label") or speaker_label)
    try:confidence=max(0.0,min(1.0,float(source.get("confidence") or 0.0)))
    except (TypeError,ValueError):confidence=0.0
    overlap=bool(source.get("overlap"))
    overlap_group=" ".join(str(source.get("overlap_group") or "").split())[:80] if overlap else ""
    diarization="provider_diarization" if source_name=="provider_diarization" else ("heuristic_acoustic" if source_name=="heuristic_acoustic" else "none")
    return {
        "contract":"speaker-attribution-v1-20261004","speaker_label":label,
        "source":source_name,"confidence":round(confidence,4),
        "participant_id":0,"participant_identity":"",
        "speaker_identity_verified":False,"identity_confidence":0.0,
        "authentication_authority":False,"visual_corroborated":False,
        "visual_conflict":False,"identity_conflict":False,
        "overlap":overlap,"overlap_group":overlap_group,
        "diarization_source":diarization,"evidence":[],
    }



def _unknown_attribution()->dict[str,Any]:
    # Keep this tiny fallback local so Section 4's isolated import harness can
    # load the transcription service without importing the full services package.
    return {
        "contract":"speaker-attribution-v1-20261004",
        "speaker_label":"Speaker 1",
        "source":"unknown",
        "confidence":0.0,
        "participant_id":0,
        "participant_identity":"",
        "speaker_identity_verified":False,
        "identity_confidence":0.0,
        "authentication_authority":False,
        "visual_corroborated":False,
        "visual_conflict":False,
        "identity_conflict":False,
        "overlap":False,
        "overlap_group":"",
        "diarization_source":"none",
        "evidence":[],
    }

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
    diarized=int(connection.execute(
        "SELECT COUNT(*) FROM local_transcription_segment_attribution WHERE session_id=? AND source='provider_diarization'",
        (row["id"],)
    ).fetchone()[0])>0
    timeline=connection.execute(
        "SELECT COALESCE(MAX(COALESCE(a.ended_ms,s.started_ms)),0) FROM local_transcription_segments s "
        "LEFT JOIN local_transcription_segment_attribution a ON a.segment_id=s.id WHERE s.session_id=?",(row["id"],)
    ).fetchone()[0]
    result={
        "id":row["id"],"title":row["title"],"status":row["status"],
        "cloud_shared":bool(row["cloud_share"]),"started_at":row["started_at"],
        "ended_at":row["ended_at"],"segment_count":int(row["segment_count"]),
        "source":"homeserver_local_transcription",
        "speaker_attribution":"provider_diarization" if diarized else "unidentified_single_channel",
        "speaker_identity_verified":False,
        "diarization_available":diarized,
        "attribution":_unknown_attribution(),
        "timeline_ms":int(timeline or 0),
    }
    if with_segments:
        segments=connection.execute(
            "SELECT s.id,s.client_key,s.text,s.started_ms,s.created_at,a.ended_ms,a.speaker_label,a.source,a.attribution_json "
            "FROM local_transcription_segments s LEFT JOIN local_transcription_segment_attribution a ON a.segment_id=s.id "
            "WHERE s.session_id=? ORDER BY s.rowid",(row["id"],)
        ).fetchall()
        payload=[]
        for index,s in enumerate(segments):
            attribution=_unknown_attribution()
            if s["attribution_json"]:
                try:
                    parsed=json.loads(s["attribution_json"])
                    attribution=_sanitize_attribution(parsed,s["speaker_label"] or "Speaker 1")
                except (ValueError,TypeError,TranscriptError):
                    attribution=_unknown_attribution()
            payload.append({
                "id":s["id"],"client_key":s["client_key"],"text":s["text"],
                "started_ms":s["started_ms"],"ended_ms":int(s["ended_ms"] if s["ended_ms"] is not None else s["started_ms"]),
                "created_at":s["created_at"],"speaker":_clean_label(s["speaker_label"] or attribution["speaker_label"]),
                "segment_index":index,
                "speaker_attribution":str(attribution["source"]),
                "speaker_identity_verified":False,
                "attribution":attribution,
            })
        result["segments"]=payload
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

def append(session_id:str,text:str,client_key:str,started_ms:int=0,*,speaker_label:str="Speaker 1",ended_ms:int|None=None,attribution:Any=None)->dict[str,Any]:
    sid=_id(session_id)
    text=str(text or "").strip()
    if not text or len(text)>MAX_TEXT:
        raise TranscriptError("Transcription segment must contain 1–8,000 characters.")
    if not isinstance(client_key,str) or not _ID.fullmatch(client_key):
        raise TranscriptError("A unique segment key is required.")
    if type(started_ms) is not int or started_ms<0 or started_ms>24*60*60*1000:
        raise TranscriptError("Invalid transcription segment timing.")
    if ended_ms is None:ended_ms=started_ms
    if type(ended_ms) is not int or ended_ms<started_ms or ended_ms>24*60*60*1000:
        raise TranscriptError("Invalid transcription segment end timing.")
    sanitized=_sanitize_attribution(attribution,speaker_label)
    speaker_label=_clean_label(sanitized["speaker_label"])
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_get(conn,sid)
        existing=conn.execute(
            "SELECT s.id,s.text,s.started_ms,a.ended_ms,a.speaker_label,a.attribution_json "
            "FROM local_transcription_segments s LEFT JOIN local_transcription_segment_attribution a ON a.segment_id=s.id "
            "WHERE s.session_id=? AND s.client_key=?",
            (sid,client_key),
        ).fetchone()
        if existing is not None:
            current=None
            if existing["attribution_json"]:
                try:current=_sanitize_attribution(json.loads(existing["attribution_json"]),existing["speaker_label"] or "Speaker 1")
                except (ValueError,TypeError,TranscriptError):current=None
            same=(existing["text"]==text and int(existing["started_ms"])==started_ms and
                  int(existing["ended_ms"] if existing["ended_ms"] is not None else existing["started_ms"])==ended_ms and
                  _clean_label(existing["speaker_label"] or "Speaker 1")==speaker_label and current==sanitized)
            legacy_same=(existing["text"]==text and int(existing["started_ms"])==started_ms and existing["attribution_json"] is None and
                         ended_ms==started_ms and sanitized["source"]=="unknown" and speaker_label=="Speaker 1")
            if not (same or legacy_same):
                raise TranscriptError("This segment key already belongs to different transcript content or speaker attribution.",409)
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
        segment_id=secrets.token_hex(16)
        created=_utc()
        conn.execute(
            "INSERT INTO local_transcription_segments"
            "(id,session_id,client_key,text,started_ms,created_at) VALUES(?,?,?,?,?,?)",
            (segment_id,sid,client_key,text,started_ms,created),
        )
        conn.execute(
            "INSERT INTO local_transcription_segment_attribution"
            "(segment_id,session_id,ended_ms,speaker_label,source,attribution_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (segment_id,sid,ended_ms,speaker_label,sanitized["source"],json.dumps(sanitized,separators=(",",":"),sort_keys=True),created),
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

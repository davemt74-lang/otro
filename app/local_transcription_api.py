"""Owner-only persistent transcription workspace, not an Agent Chat command stream."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from .services import local_transcription_sessions as sessions
router=APIRouter(prefix="/api/v1/control/transcription-sessions",tags=["local-transcriptions"])

class Start(BaseModel):
    model_config=ConfigDict(extra="forbid")
    title:str=Field(default="Untitled transcription",max_length=190)

class Segment(BaseModel):
    model_config=ConfigDict(extra="forbid")
    text:str=Field(min_length=1,max_length=8000)
    client_key:str=Field(min_length=32,max_length=32)
    started_ms:int=Field(default=0,ge=0,le=86400000)
    ended_ms:int|None=Field(default=None,ge=0,le=86400000)
    speaker_label:str=Field(default="Speaker 1",max_length=80)
    attribution:dict[str,Any]|None=None

class Share(BaseModel):
    model_config=ConfigDict(extra="forbid")
    cloud_share:bool

def _mutation(request:Request,requested_with:str|None)->None:
    if requested_with!="XMLHttpRequest":
        raise HTTPException(403,detail="Owner UI request required.")
    origin=request.headers.get("origin")
    if origin and origin.rstrip("/")!=str(request.base_url).rstrip("/"):
        raise HTTPException(403,detail="Transcription origin mismatch.")

def _call(func,*args,**kwargs):
    try:return func(*args,**kwargs)
    except sessions.TranscriptError as exc:
        raise HTTPException(exc.status_code,detail=str(exc)) from exc

@router.get("")
def list_local_sessions()->dict:
    return _call(sessions.list_sessions)

@router.get("/{session_id}")
def read_local_session(session_id:str)->dict:
    return _call(sessions.get,session_id)

@router.post("")
def start_local_session(body:Start,request:Request,requested_with:str|None=Header(None,alias="X-Requested-With"))->dict:
    _mutation(request,requested_with)
    return _call(sessions.start,body.title)

@router.post("/{session_id}/segments")
def append_local_session(session_id:str,body:Segment,request:Request,requested_with:str|None=Header(None,alias="X-Requested-With"))->dict:
    _mutation(request,requested_with)
    return _call(sessions.append,session_id,body.text,body.client_key,body.started_ms,
                 speaker_label=body.speaker_label,ended_ms=body.ended_ms,attribution=body.attribution)

@router.post("/{session_id}/stop")
def stop_local_session(session_id:str,request:Request,requested_with:str|None=Header(None,alias="X-Requested-With"))->dict:
    _mutation(request,requested_with)
    return _call(sessions.stop,session_id)

@router.put("/{session_id}/cloud-share")
def share_local_session(session_id:str,body:Share,request:Request,requested_with:str|None=Header(None,alias="X-Requested-With"))->dict:
    _mutation(request,requested_with)
    return _call(sessions.share_with_cloud,session_id,body.cloud_share)

@router.delete("/{session_id}")
def delete_local_session(session_id:str,request:Request,requested_with:str|None=Header(None,alias="X-Requested-With"))->dict:
    _mutation(request,requested_with)
    return _call(sessions.delete,session_id)

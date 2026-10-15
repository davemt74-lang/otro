from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .services import members, stonefellow_native

ROOT_DIR=Path(__file__).resolve().parents[1]
UI_DIR=ROOT_DIR/"ui"
COOKIE_NAME="homeserver_member"

router=APIRouter()


class MemberCreate(BaseModel):
    username:str=Field(min_length=3,max_length=40)
    display_name:str=Field(min_length=1,max_length=120)
    password:str=Field(min_length=10,max_length=256)
    role:str=Field(default="member",pattern="^(admin|member|guest)$")


class MemberUpdate(BaseModel):
    display_name:str|None=Field(default=None,min_length=1,max_length=120)
    role:str|None=Field(default=None,pattern="^(admin|member|guest)$")
    status:str|None=Field(default=None,pattern="^(active|disabled)$")


class PasswordUpdate(BaseModel):
    password:str=Field(min_length=10,max_length=256)


class SessionCreate(BaseModel):
    username:str=Field(min_length=3,max_length=40)
    password:str=Field(min_length=1,max_length=256)


class ContextUpdate(BaseModel):
    value:Any


class AppAccessUpdate(BaseModel):
    allowed:bool


def _raise(exc:members.MemberError):
    raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


def _identity(token:str|None)->dict:
    try:
        return members.session_identity(token)
    except members.MemberError as exc:
        _raise(exc)


@router.get("/member",include_in_schema=False)
def member_workspace():
    page=UI_DIR/"member.html"
    if not page.is_file():
        raise HTTPException(status_code=503,detail="Member workspace assets are unavailable")
    return FileResponse(page)


@router.post("/api/v1/member/session")
def create_session(payload:SessionCreate,response:Response)->dict:
    try:
        result=members.authenticate(payload.username,payload.password)
    except members.MemberError as exc:
        _raise(exc)
    response.set_cookie(
        COOKIE_NAME,
        result["session_token"],
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
        max_age=12*60*60,
    )
    return {"member":result["member"],"expires_at":result["expires_at"]}


@router.delete("/api/v1/member/session")
def delete_session(response:Response,homeserver_member:str|None=Cookie(default=None))->dict:
    members.revoke_session(homeserver_member)
    response.delete_cookie(COOKIE_NAME,path="/",samesite="strict")
    return {"signed_out":True}


@router.get("/api/v1/member/me")
def member_me(homeserver_member:str|None=Cookie(default=None))->dict:
    return {"member":_identity(homeserver_member)}


@router.get("/api/v1/member/apps")
def member_apps(homeserver_member:str|None=Cookie(default=None))->dict:
    identity=_identity(homeserver_member)
    try:
        return members.assigned_apps(identity["member_id"])
    except members.MemberError as exc:
        _raise(exc)


@router.get("/api/v1/member/context")
def member_context(homeserver_member:str|None=Cookie(default=None))->dict:
    identity=_identity(homeserver_member)
    try:
        return members.context(identity["member_id"])
    except members.MemberError as exc:
        _raise(exc)


@router.put("/api/v1/member/context/{context_key}")
def member_context_update(context_key:str,payload:ContextUpdate,homeserver_member:str|None=Cookie(default=None))->dict:
    identity=_identity(homeserver_member)
    try:
        return members.set_context(identity["member_id"],context_key,payload.value)
    except members.MemberError as exc:
        _raise(exc)


@router.delete("/api/v1/member/context/{context_key}")
def member_context_delete(context_key:str,homeserver_member:str|None=Cookie(default=None))->dict:
    identity=_identity(homeserver_member)
    try:
        deleted=members.delete_context(identity["member_id"],context_key)
    except members.MemberError as exc:
        _raise(exc)
    if not deleted:
        raise HTTPException(status_code=404,detail="Member context item not found.")
    return {"deleted":True}


@router.get("/api/v1/member/activity")
def member_activity(
    limit:int=Query(default=100,ge=1,le=250),
    homeserver_member:str|None=Cookie(default=None),
)->dict:
    identity=_identity(homeserver_member)
    try:
        return members.member_activity(identity["member_id"],limit)
    except members.MemberError as exc:
        _raise(exc)


@router.get("/api/v1/member/agent-context")
def member_agent_context(homeserver_member:str|None=Cookie(default=None))->dict:
    identity=_identity(homeserver_member)
    try:
        result=members.brain_context(identity["member_id"])
        if any(app.get("app_key")==stonefellow_native.APP_KEY for app in result["apps"]):
            try:
                result["stonefellow_music"]=stonefellow_native.fan_agent_context(identity["member_id"])
            except stonefellow_native.StonefellowError:
                result["stonefellow_music"]={"status":"unavailable","read_only":True}
        return result
    except members.MemberError as exc:
        _raise(exc)


class StonefellowAdminSite(BaseModel):
    origin:str=Field(min_length=12,max_length=250)


def _stonefellow_member_admin(token:str|None)->dict:
    identity=_identity(token)
    if identity.get("role")!="admin":
        raise HTTPException(status_code=403,detail="HomeServer member administrator access required.")
    try:
        stonefellow_native._allowed_member(identity["member_id"])
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    return identity


@router.put("/api/v1/member/stonefellow/admin/origin")
def member_stonefellow_admin_origin(payload:StonefellowAdminSite,
                                     homeserver_member:str|None=Cookie(default=None))->dict:
    _stonefellow_member_admin(homeserver_member)
    try:
        return stonefellow_native.configure_origin(payload.origin)
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.post("/api/v1/member/stonefellow/admin/authorize")
def member_stonefellow_admin_start(homeserver_member:str|None=Cookie(default=None))->dict:
    _stonefellow_member_admin(homeserver_member)
    try:
        return stonefellow_native.start_pair("admin.presence.read",label="VP3 HomeServer Owner")
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.post("/api/v1/member/stonefellow/admin/complete")
def member_stonefellow_admin_finish(homeserver_member:str|None=Cookie(default=None))->dict:
    _stonefellow_member_admin(homeserver_member)
    try:
        return stonefellow_native.finish_pair("admin.presence.read")
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/api/v1/member/stonefellow/admin/status")
def member_stonefellow_admin_status(homeserver_member:str|None=Cookie(default=None))->dict:
    _stonefellow_member_admin(homeserver_member)
    try:
        return stonefellow_native.admin_status()
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.delete("/api/v1/member/stonefellow/admin")
def member_stonefellow_admin_disconnect(homeserver_member:str|None=Cookie(default=None))->dict:
    _stonefellow_member_admin(homeserver_member)
    try:
        return stonefellow_native.admin_disconnect()
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.post("/api/v1/member/stonefellow/authorize")
def member_stonefellow_start(homeserver_member:str|None=Cookie(default=None))->dict:
    member=_identity(homeserver_member)
    try:
        return stonefellow_native.start_pair("fan.library.read",member_id=member["member_id"],
                                             label="VP3 HomeServer Music")
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.post("/api/v1/member/stonefellow/complete")
def member_stonefellow_finish(homeserver_member:str|None=Cookie(default=None))->dict:
    member=_identity(homeserver_member)
    try:
        return stonefellow_native.finish_pair("fan.library.read",member_id=member["member_id"])
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/api/v1/member/stonefellow/library")
def member_stonefellow_library(homeserver_member:str|None=Cookie(default=None))->dict:
    member=_identity(homeserver_member)
    try:
        return stonefellow_native.fan_library(member["member_id"])
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc



@router.get("/api/v1/member/stonefellow/audio/{track_id}")
def member_stonefellow_audio(track_id:str,
                            range_header:str|None=Header(default=None,alias="Range"),
                            homeserver_member:str|None=Cookie(default=None)):
    identity=_identity(homeserver_member)
    try:
        audio=stonefellow_native.fan_audio(identity["member_id"],track_id,
                                           range_header=range_header or "")
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    return Response(content=audio["body"],status_code=audio["status_code"],
                    media_type=audio["media_type"],headers=audio["headers"])



@router.get("/api/v1/member/stonefellow/download/{track_id}")
def member_stonefellow_download(track_id:str,
                                homeserver_member:str|None=Cookie(default=None)):
    identity=_identity(homeserver_member)
    try:
        first=stonefellow_native.fan_download_prepare(identity["member_id"],track_id)
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    ext="mp3" if first["media_type"]=="audio/mpeg" else "wav"
    return StreamingResponse(
        stonefellow_native.fan_download_chunks(identity["member_id"],track_id,first),
        media_type=first["media_type"],
        headers={"Cache-Control":"private, no-store",
                 "X-Content-Type-Options":"nosniff",
                 "Content-Length":str(first["total_bytes"]),
                 "Content-Disposition":f'attachment; filename="stonefellow-{track_id}.{ext}"'})


@router.delete("/api/v1/member/stonefellow")
def member_stonefellow_disconnect(homeserver_member:str|None=Cookie(default=None))->dict:
    member=_identity(homeserver_member)
    try:
        return stonefellow_native.fan_disconnect(member["member_id"])
    except stonefellow_native.StonefellowError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/api/v1/control/members")
def control_members()->dict:
    return members.list_members()


@router.post("/api/v1/control/members")
def control_member_create(payload:MemberCreate)->dict:
    try:
        return {"member":members.create_member(
            payload.username,payload.display_name,payload.password,payload.role
        )}
    except members.MemberError as exc:
        _raise(exc)


@router.patch("/api/v1/control/members/{member_id}")
def control_member_update(member_id:str,payload:MemberUpdate)->dict:
    try:
        return {"member":members.update_member(member_id,payload.model_dump(exclude_none=True))}
    except members.MemberError as exc:
        _raise(exc)


@router.put("/api/v1/control/members/{member_id}/password")
def control_member_password(member_id:str,payload:PasswordUpdate)->dict:
    try:
        return members.set_password(member_id,payload.password)
    except members.MemberError as exc:
        _raise(exc)


@router.get("/api/v1/control/members/{member_id}/apps")
def control_member_apps(member_id:str)->dict:
    try:
        return members.app_access_matrix(member_id)
    except members.MemberError as exc:
        _raise(exc)


@router.put("/api/v1/control/members/{member_id}/apps/{app_key}")
def control_member_app_access(member_id:str,app_key:str,payload:AppAccessUpdate)->dict:
    try:
        return members.set_app_access(member_id,app_key,payload.allowed)
    except members.MemberError as exc:
        _raise(exc)


@router.get("/api/v1/control/members/{member_id}/agent-context")
def control_member_agent_context(member_id:str)->dict:
    try:
        return members.brain_context(member_id)
    except members.MemberError as exc:
        _raise(exc)


@router.get("/api/v1/control/members/capability")
def control_member_capability()->dict:
    return members.public_capability()

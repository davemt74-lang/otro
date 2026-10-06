from __future__ import annotations
from fastapi.responses import FileResponse
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from .services import workspace_sync

router=APIRouter(prefix='/api/v1/control/workspace-sync',tags=['workspace-sync'])

class SyncSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool


def mutation(request:Request, requested_with:str|None)->None:
    if requested_with!='XMLHttpRequest':
        raise HTTPException(403,detail='Owner UI request required.')
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,detail='Workspace origin mismatch.')

@router.get('')
def status()->dict:
    return workspace_sync.status()

@router.put('/settings')
def settings(body:SyncSettings,request:Request,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with)
    return workspace_sync.set_enabled(body.enabled)

@router.post('/refresh')
def refresh(request:Request,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with)
    workspace_sync.wake()
    return {'queued':True,**workspace_sync.status()}

@router.get('/records/{dataset}')
def records(dataset:str,q:str=Query(default='',max_length=240),offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100),key:str=Query(default='',max_length=240))->dict:
    try:
        return workspace_sync.records(dataset,q,offset,limit,detail_key=key)
    except workspace_sync.WorkspaceSyncError as exc:
        raise HTTPException(422,detail=str(exc)) from exc

@router.get('/assets/{sha256}')
def asset(sha256:str):
    try:
        path,name=workspace_sync.asset(sha256)
        return FileResponse(path,filename=name,headers={'Cache-Control':'no-store'})
    except workspace_sync.WorkspaceSyncError as exc:
        raise HTTPException(404,detail=str(exc)) from exc

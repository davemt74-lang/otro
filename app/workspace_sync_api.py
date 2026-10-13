from __future__ import annotations
from fastapi.responses import FileResponse, RedirectResponse
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from .services import workspace_sync, native_workspaces, workspace_actions
from .services import browser_captures_r4, project_knowledge_r5, project_knowledge_r6, project_knowledge_r7

router=APIRouter(prefix='/api/v1/control/workspace-sync',tags=['workspace-sync'])

@router.get('/browser-captures')
def browser_captures(response:Response,local_conversation:str=Query(default='',max_length=100),agent_id:int=Query(default=0,ge=0))->dict:
    response.headers['Cache-Control']='no-store'
    try:
        return browser_captures_r4.captures(local_conversation,agent_id)
    except workspace_sync.WorkspaceSyncError as exc:
        raise HTTPException(exc.status_code,detail=str(exc)) from exc

@router.post('/project-knowledge')
async def project_knowledge(request:Request,response:Response,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with)
    response.headers['Cache-Control']='no-store'
    raw=await request.body()
    if len(raw)>16384:raise HTTPException(413,detail='Annotation request is too large.')
    import json
    try:
        body=json.loads(raw)
        if not isinstance(body,dict):raise ValueError()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(project_knowledge_r5.request,body)
    except (ValueError,TypeError) as exc:raise HTTPException(422,detail='Invalid annotation request.') from exc
    except workspace_sync.WorkspaceSyncError as exc:raise HTTPException(exc.status_code,detail=str(exc)) from exc

@router.post('/project-context')
async def project_context(request:Request,response:Response,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with);response.headers['Cache-Control']='no-store'
    raw=await request.body()
    if len(raw)>32000:raise HTTPException(413,detail='Context request is too large.')
    import json
    try:
        body=json.loads(raw)
        if not isinstance(body,dict):raise ValueError()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(project_knowledge_r6.request,body)
    except (ValueError,TypeError) as exc:raise HTTPException(422,detail='Invalid context request.') from exc
    except workspace_sync.WorkspaceSyncError as exc:raise HTTPException(exc.status_code,detail=str(exc)) from exc

@router.post('/project-refresh')
async def project_refresh(request:Request,response:Response,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with);response.headers['Cache-Control']='no-store'
    raw=await request.body()
    if len(raw)>32000:raise HTTPException(413,detail='Context request is too large.')
    import json
    try:
        body=json.loads(raw)
        if not isinstance(body,dict):raise ValueError()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(project_knowledge_r7.request,body)
    except (ValueError,TypeError) as exc:raise HTTPException(422,detail='Invalid context request.') from exc
    except workspace_sync.WorkspaceSyncError as exc:raise HTTPException(exc.status_code,detail=str(exc)) from exc

class SyncSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool

class WorkspaceEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset:str
    key:str
    expected_revision:str
    mutation_id:str
    fields:dict

@router.get('/edit/{dataset}')
def editable(dataset:str,key:str=Query(max_length=240))->dict:
    try:
        return workspace_actions.editable(dataset,key)
    except workspace_actions.WorkspaceActionError as exc:
        raise HTTPException(exc.status_code,detail=str(exc)) from exc

@router.post('/edits')
def save_edit(body:WorkspaceEdit,request:Request,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with)
    try:
        return workspace_actions.enqueue(body.model_dump())
    except workspace_actions.WorkspaceActionError as exc:
        raise HTTPException(exc.status_code,detail=str(exc)) from exc

@router.get('/edits')
def edits()->dict:
    return workspace_actions.status()

@router.post('/edits/{mutation_id}/cancel')
def cancel_edit(mutation_id:str,request:Request,x_requested_with:str|None=Header(default=None))->dict:
    mutation(request,x_requested_with)
    try:
        return workspace_actions.cancel(mutation_id)
    except workspace_actions.WorkspaceActionError as exc:
        raise HTTPException(exc.status_code,detail=str(exc)) from exc


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

@router.get('/native/{dataset}')
def native_records(dataset:str,q:str=Query(default='',max_length=240),offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=500))->dict:
    try:
        return native_workspaces.items(dataset,q,offset,limit)
    except workspace_sync.WorkspaceSyncError as exc:
        raise HTTPException(422,detail=str(exc)) from exc

@router.get('/source/{dataset}')
def native_source(dataset:str,key:str=Query(max_length=240)):
    try:
        return RedirectResponse(native_workspaces.source_url(dataset,key),status_code=303,headers={'Cache-Control':'no-store'})
    except workspace_sync.WorkspaceSyncError as exc:
        raise HTTPException(409,detail=str(exc)) from exc

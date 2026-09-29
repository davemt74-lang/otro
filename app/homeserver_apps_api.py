from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from .services import homeserver_app_packages, homeserver_app_resources, homeserver_app_runtime, homeserver_app_security, homeserver_apps

router=APIRouter(prefix="/api/v1/control/homeserver-apps",tags=["homeserver-apps"])


class CreateUserAppRequest(BaseModel):
    app_key:str=Field(min_length=2,max_length=80)
    name:str=Field(min_length=1,max_length=160)
    source_type:str=Field(default="user_created",max_length=40)
    runtime:str=Field(default="static",pattern="^(static|php)$")
    source_ref:str=Field(default="",max_length=500)
    metadata:dict=Field(default_factory=dict)


class LifecycleRequest(BaseModel):
    state:str=Field(min_length=3,max_length=40)
    metadata:dict=Field(default_factory=dict)


class PermissionDecisionRequest(BaseModel):
    permission:str=Field(min_length=1,max_length=120)
    allowed:bool


class SecretValueRequest(BaseModel):
    value:str=Field(min_length=1,max_length=16000)


class ResourceLimitsRequest(BaseModel):
    storage_limit_bytes:int|None=Field(default=None,ge=1)
    sqlite_limit_bytes:int|None=Field(default=None,ge=1)


class AppEventRequest(BaseModel):
    topic:str=Field(min_length=1,max_length=160)
    payload:dict=Field(default_factory=dict)


def _call(operation,*args,**kwargs):  # noqa: ANN001,ANN201
    try:
        return operation(*args,**kwargs)
    except homeserver_apps.HomeServerAppError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_packages.AppPackageError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_security.AppSecurityError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_resources.AppResourceError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_runtime.AppRuntimeError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("")
def list_apps()->dict:
    return homeserver_apps.list_apps()


@router.get("/capability")
def apps_capability()->dict:
    return {**homeserver_apps.public_capability(),"packages":homeserver_app_packages.public_capability(),"security":homeserver_app_security.public_capability(),"resources":homeserver_app_resources.public_capability(),"runtime_services":homeserver_app_runtime.public_capability()}


@router.post("")
def create_user_app(payload:CreateUserAppRequest)->dict:
    return _call(
        homeserver_apps.create_user_app,
        payload.app_key,
        payload.name,
        runtime=payload.runtime,
        source_type=payload.source_type,
        metadata=payload.metadata,
    )


@router.get("/{app_key}")
def app_detail(app_key:str)->dict:
    return {"app":_call(homeserver_apps.get,app_key),"history":_call(homeserver_apps.history,app_key,100)}


@router.post("/{app_key}/lifecycle")
def app_lifecycle(app_key:str,payload:LifecycleRequest)->dict:
    return {"app":_call(homeserver_apps.transition,app_key,payload.state,metadata=payload.metadata)}


@router.post("/{app_key}/package/validate")
async def validate_app_package(app_key:str,file:UploadFile=File(...))->dict:
    package=await file.read(homeserver_app_packages.MAX_PACKAGE_BYTES+1)
    if len(package)>homeserver_app_packages.MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413,detail="App package exceeds the compressed size limit.")
    return {"validation":_call(homeserver_app_packages.validate_package,package,expected_app_key=app_key)}


@router.post("/{app_key}/package/install")
async def install_app_package(app_key:str,file:UploadFile=File(...))->dict:
    package=await file.read(homeserver_app_packages.MAX_PACKAGE_BYTES+1)
    if len(package)>homeserver_app_packages.MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413,detail="App package exceeds the compressed size limit.")
    return {"release":_call(homeserver_app_packages.install_package,app_key,package,source_type="zip")}


@router.get("/{app_key}/runtime")
def app_runtime_status(app_key:str)->dict:
    return {"runtime":_call(homeserver_app_packages.runtime_status,app_key)}


@router.post("/{app_key}/build-install")
def build_install_user_app(app_key:str)->dict:
    return {"release":_call(homeserver_app_packages.install_project,app_key)}


@router.get("/{app_key}/permissions")
def app_permissions(app_key:str)->dict:
    return {"permissions":_call(homeserver_app_security.permission_status,app_key)}


@router.put("/{app_key}/permissions")
def app_permission_update(app_key:str,payload:PermissionDecisionRequest)->dict:
    return {"permissions":_call(homeserver_app_security.set_permission,app_key,payload.permission,payload.allowed)}


@router.get("/{app_key}/secrets")
def app_secret_status(app_key:str)->dict:
    return {"secrets":_call(homeserver_app_security.secret_status,app_key)}


@router.put("/{app_key}/secrets/{secret_key}")
def app_secret_set(app_key:str,secret_key:str,payload:SecretValueRequest)->dict:
    return {"secrets":_call(homeserver_app_security.set_secret,app_key,secret_key,payload.value)}


@router.delete("/{app_key}/secrets/{secret_key}")
def app_secret_delete(app_key:str,secret_key:str)->dict:
    return {"secrets":_call(homeserver_app_security.remove_secret,app_key,secret_key)}


@router.get("/{app_key}/resources")
def app_resource_status(app_key:str)->dict:
    return {"resources":_call(homeserver_app_resources.resource_status,app_key)}


@router.put("/{app_key}/resources")
def app_resource_limits_update(app_key:str,payload:ResourceLimitsRequest)->dict:
    return {"resources":_call(
        homeserver_app_resources.update_limits,
        app_key,
        storage_limit_bytes=payload.storage_limit_bytes,
        sqlite_limit_bytes=payload.sqlite_limit_bytes,
    )}


@router.get("/{app_key}/runtime/services")
def app_runtime_services(app_key:str)->dict:
    return {"runtime":_call(homeserver_app_runtime.runtime_status,app_key)}


@router.get("/{app_key}/runtime/events")
def app_events(app_key:str,topic:str=Query(default="",max_length=160),limit:int=Query(default=100,ge=1,le=500))->dict:
    return {"events":_call(homeserver_app_runtime.list_events,app_key,topic=topic,limit=limit)}


@router.post("/{app_key}/runtime/events")
def app_event_publish(app_key:str,payload:AppEventRequest)->dict:
    return {"event":_call(homeserver_app_runtime.publish_event,app_key,payload.topic,payload.payload,source="owner")}


@router.get("/{app_key}/runtime/jobs")
def app_jobs(app_key:str)->dict:
    return {"jobs":_call(homeserver_app_runtime.list_jobs,app_key)}


@router.post("/{app_key}/runtime/jobs/{job_id}/run")
def app_job_run(app_key:str,job_id:str)->dict:
    return {"run":_call(homeserver_app_runtime.run_job,app_key,job_id)}


@router.put("/{app_key}/data/file")
async def app_data_write(app_key:str,path:str=Query(min_length=1,max_length=1000),file:UploadFile=File(...))->dict:
    data=await file.read(16*1024*1024+1)
    if len(data)>16*1024*1024:
        raise HTTPException(status_code=413,detail="App data upload exceeds the request limit.")
    return {"file":_call(homeserver_app_resources.write_file,app_key,path,data)}


@router.get("/{app_key}/data/file")
def app_data_read(app_key:str,path:str=Query(min_length=1,max_length=1000)):
    data=_call(homeserver_app_resources.read_file,app_key,path,16*1024*1024)
    from fastapi.responses import Response
    return Response(content=data,media_type="application/octet-stream",headers={"Cache-Control":"no-store"})


@router.delete("/{app_key}/data/file")
def app_data_delete(app_key:str,path:str=Query(min_length=1,max_length=1000))->dict:
    return {"deleted":_call(homeserver_app_resources.delete_file,app_key,path)}


@router.api_route("/{app_key}/preview/{request_path:path}",methods=["GET","HEAD","POST"],include_in_schema=False)
async def app_preview(app_key:str,request_path:str,request:Request):
    content_length=request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length)>8*1024*1024:
                raise HTTPException(status_code=413,detail="App request body exceeds the runtime limit.")
        except ValueError as exc:
            raise HTTPException(status_code=400,detail="Invalid Content-Length header.") from exc
    body=await request.body()
    if len(body)>8*1024*1024:
        raise HTTPException(status_code=413,detail="App request body exceeds the runtime limit.")
    return _call(
        homeserver_app_runtime.serve,
        app_key,
        request_path,
        method=request.method,
        query_string=request.url.query,
        content_type=request.headers.get("content-type"),
        body=body,
    )

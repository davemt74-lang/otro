from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .services import homeserver_app_packages, homeserver_apps

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


def _call(operation,*args,**kwargs):  # noqa: ANN001,ANN201
    try:
        return operation(*args,**kwargs)
    except homeserver_apps.HomeServerAppError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_packages.AppPackageError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("")
def list_apps()->dict:
    return homeserver_apps.list_apps()


@router.get("/capability")
def apps_capability()->dict:
    return homeserver_apps.public_capability()


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

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .services import homeserver_app_packages, homeserver_app_resources, homeserver_app_security, homeserver_apps

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


@router.get("")
def list_apps()->dict:
    return homeserver_apps.list_apps()


@router.get("/capability")
def apps_capability()->dict:
    return {**homeserver_apps.public_capability(),"packages":homeserver_app_packages.public_capability(),"security":homeserver_app_security.public_capability(),"resources":homeserver_app_resources.public_capability()}


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

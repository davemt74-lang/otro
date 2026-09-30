from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from .services import homeserver_app_agent, homeserver_app_distribution, homeserver_app_manager, homeserver_app_packages, homeserver_app_platform, homeserver_app_prebuilt, homeserver_app_releases, homeserver_app_resources, homeserver_app_runtime, homeserver_app_sample_data, homeserver_app_security, homeserver_app_sources, homeserver_app_workspace, homeserver_apps, homeserver_media_server

router=APIRouter(prefix="/api/v1/control/homeserver-apps",tags=["homeserver-apps"])


class CreateUserAppRequest(BaseModel):
    app_key:str=Field(min_length=2,max_length=80)
    name:str=Field(min_length=1,max_length=160)
    source_type:str=Field(default="user_created",max_length=40)
    runtime:str=Field(default="static",pattern="^(static|php)$")
    source_ref:str=Field(default="",max_length=500)
    metadata:dict=Field(default_factory=dict)
    permissions:list[str]=Field(default_factory=list,max_length=64)


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


class SampleDataSettingsRequest(BaseModel):
    enabled:bool


class AppEventRequest(BaseModel):
    topic:str=Field(min_length=1,max_length=160)
    payload:dict=Field(default_factory=dict)


class GitSourceInspectRequest(BaseModel):
    repo_url:str=Field(min_length=8,max_length=1000)
    ref:str=Field(default="HEAD",min_length=1,max_length=160)


class SourceInstallRequest(BaseModel):
    approved:bool=False


class SourceRefreshRequest(BaseModel):
    ref:str=Field(default="",max_length=160)


class SourceDetachRequest(BaseModel):
    confirmed:bool=False


class WorkspaceWriteRequest(BaseModel):
    path:str=Field(min_length=1,max_length=1000)
    content:str=Field(max_length=2*1024*1024)


class MediaRootGrantRequest(BaseModel):
    path:str=Field(min_length=1,max_length=2000)
    label:str=Field(default="",max_length=120)


class MediaPlaybackRequest(BaseModel):
    position_seconds:float=Field(default=0,ge=0)
    duration_seconds:float=Field(default=0,ge=0)
    completed:bool=False


class WorkspaceRenameRequest(BaseModel):
    path:str=Field(min_length=1,max_length=1000)
    new_path:str=Field(min_length=1,max_length=1000)


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
    except homeserver_app_sample_data.AppSampleDataError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_releases.AppReleaseError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_sources.AppSourceError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_workspace.AppWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_app_distribution.AppDistributionError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_media_server.MediaServerError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("")
def list_apps()->dict:
    return homeserver_apps.list_apps()


@router.get("/capability")
def apps_capability()->dict:
    return {**homeserver_apps.public_capability(),"platform":homeserver_app_platform.capability(),"manager":homeserver_app_manager.public_capability(),"media_server":homeserver_media_server.public_capability(),"packages":homeserver_app_packages.public_capability(),"security":homeserver_app_security.public_capability(),"resources":homeserver_app_resources.public_capability(),"runtime_services":homeserver_app_runtime.public_capability(),"sample_data":homeserver_app_sample_data.public_capability(),"prebuilt":homeserver_app_prebuilt.public_capability(),"agent":homeserver_app_agent.public_capability(),"releases":homeserver_app_releases.public_capability(),"sources":homeserver_app_sources.public_capability(),"workspace":homeserver_app_workspace.public_capability(),"distribution":homeserver_app_distribution.public_capability()}


@router.get("/platform")
def app_platform_capability()->dict:
    return homeserver_app_platform.capability()


@router.get("/manager")
def app_manager_inventory()->dict:
    return homeserver_app_manager.inventory()


@router.get("/manager/{app_key}")
def app_manager_item(app_key:str)->dict:
    return {"app":_call(homeserver_app_manager.app,app_key)}


@router.get("/media-server/capability")
def media_server_capability()->dict:
    return homeserver_media_server.public_capability()


@router.get("/media-server/status")
def media_server_status()->dict:
    return _call(homeserver_media_server.status)


@router.get("/media-server/roots")
def media_server_roots()->dict:
    return _call(homeserver_media_server.roots)


@router.post("/media-server/roots")
def media_server_add_root(payload:MediaRootGrantRequest)->dict:
    return _call(homeserver_media_server.add_root,payload.path,payload.label)


@router.delete("/media-server/roots/{root_id}")
def media_server_remove_root(root_id:str)->dict:
    return _call(homeserver_media_server.remove_root,root_id)


@router.post("/media-server/scan")
def media_server_scan(root_id:str=Query(default="",max_length=64))->dict:
    return _call(homeserver_media_server.scan,root_id)


@router.get("/media-server/library")
def media_server_library(
    q:str=Query(default="",max_length=200),
    media_type:str=Query(default="",max_length=20),
    limit:int=Query(default=100,ge=1,le=500),
    offset:int=Query(default=0,ge=0,le=1000000),
)->dict:
    return _call(homeserver_media_server.library,q,media_type,limit,offset)


@router.get("/media-server/item/{media_id}")
def media_server_item(media_id:str)->dict:
    return _call(homeserver_media_server.item,media_id)


@router.get("/media-server/stream/{media_id}")
def media_server_stream(media_id:str):
    path,mime,item=_call(homeserver_media_server.resolve_stream,media_id)
    from fastapi.responses import FileResponse
    return FileResponse(
        path,
        media_type=mime,
        filename=None,
        headers={
            "Accept-Ranges":"bytes",
            "Cache-Control":"private, no-store",
            "X-Content-Type-Options":"nosniff",
            "X-VP3-Media-Id":str(item["media_id"]),
        },
    )


@router.put("/media-server/playback/{media_id}")
def media_server_playback(media_id:str,payload:MediaPlaybackRequest)->dict:
    return _call(
        homeserver_media_server.update_playback,
        media_id,
        payload.position_seconds,
        payload.duration_seconds,
        payload.completed,
    )


@router.get("/media-server/remote")
def media_server_remote_status()->dict:
    return _call(homeserver_media_server.remote_status)


@router.post("/media-server/remote/enable")
def media_server_remote_enable()->dict:
    return _call(homeserver_media_server.enable_remote_access)


@router.post("/media-server/remote/disable")
def media_server_remote_disable()->dict:
    return _call(homeserver_media_server.disable_remote_access)


@router.get("/permissions/catalog")
def app_permission_catalog()->dict:
    return homeserver_app_security.permission_catalog()


@router.get("/catalog/prebuilt")
def prebuilt_apps_catalog()->dict:
    return homeserver_app_prebuilt.catalog()


@router.post("/catalog/prebuilt/{catalog_key}/install")
def install_prebuilt_app(catalog_key:str)->dict:
    return _call(homeserver_app_prebuilt.install,catalog_key)


@router.post("/sources/zip/inspect")
async def inspect_zip_source(file:UploadFile=File(...))->dict:
    package=await file.read(homeserver_app_packages.MAX_PACKAGE_BYTES+1)
    if len(package)>homeserver_app_packages.MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413,detail="App package exceeds the compressed size limit.")
    return {"source":_call(homeserver_app_sources.inspect_zip,package,file.filename or "package.zip")}


@router.post("/sources/git/inspect")
def inspect_git_source(payload:GitSourceInspectRequest)->dict:
    return {"source":_call(homeserver_app_sources.inspect_git,payload.repo_url,payload.ref)}


@router.post("/sources/{source_id}/install")
def install_inspected_source(source_id:str,payload:SourceInstallRequest)->dict:
    return _call(homeserver_app_sources.install_source,source_id,approved=payload.approved)


@router.post("")
def create_user_app(payload:CreateUserAppRequest)->dict:
    return _call(
        homeserver_apps.create_user_app,
        payload.app_key,
        payload.name,
        runtime=payload.runtime,
        source_type=payload.source_type,
        metadata=payload.metadata,
        permissions=homeserver_app_security.normalize_declared_permissions(payload.permissions),
    )


@router.get("/{app_key}/distribution")
def app_distribution_descriptor(app_key:str)->dict:
    result=_call(homeserver_app_distribution.distribution_descriptor,app_key)
    return {"distribution":result["descriptor"]}


@router.get("/{app_key}/distribution/export")
def app_distribution_export(app_key:str):
    exported=_call(homeserver_app_distribution.export_bundle,app_key)
    from fastapi.responses import Response
    return Response(
        content=exported["bundle"],
        media_type="application/zip",
        headers={
            "Content-Disposition":f'attachment; filename="{exported["file_name"]}"',
            "X-VP3-Package-SHA256":str(exported["descriptor"]["package_sha256"]),
            "X-VP3-Bundle-SHA256":str(exported["bundle_sha256"]),
            "Cache-Control":"no-store",
        },
    )


@router.post("/distribution/inspect")
async def app_distribution_inspect(
    file:UploadFile=File(...),
    expected_package_sha256:str=Query(default="",max_length=64),
)->dict:
    bundle=await file.read(homeserver_app_distribution.MAX_BUNDLE_BYTES+1)
    if len(bundle)>homeserver_app_distribution.MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=413,detail="Distribution bundle exceeds the size limit.")
    review=_call(
        homeserver_app_distribution.preview_bundle,
        bundle,
        expected_package_sha256=expected_package_sha256,
    )
    return {"distribution":review}


@router.get("/{app_key}/distribution/provenance")
def app_distribution_provenance(app_key:str)->dict:
    return {"distribution":_call(homeserver_app_distribution.installed_provenance,app_key)}


@router.post("/distribution/install")
async def app_distribution_install(
    file:UploadFile=File(...),
    approved:bool=Query(default=False),
    expected_package_sha256:str=Query(default="",max_length=64),
    share_public_id:str=Query(default="",max_length=64),
)->dict:
    bundle=await file.read(homeserver_app_distribution.MAX_BUNDLE_BYTES+1)
    if len(bundle)>homeserver_app_distribution.MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=413,detail="Distribution bundle exceeds the size limit.")
    return {"distribution":_call(
        homeserver_app_distribution.install_bundle,
        bundle,
        approved=approved,
        expected_package_sha256=expected_package_sha256,
        share_public_id=share_public_id,
    )}


@router.get("/{app_key}/workspace")
def app_workspace_status(app_key:str)->dict:
    return _call(homeserver_app_workspace.status,app_key)


@router.get("/{app_key}/workspace/files")
def app_workspace_files(app_key:str)->dict:
    return _call(homeserver_app_workspace.list_files,app_key)


@router.get("/{app_key}/workspace/file")
def app_workspace_file(app_key:str,path:str=Query(min_length=1,max_length=1000))->dict:
    return _call(homeserver_app_workspace.read_file,app_key,path)


@router.put("/{app_key}/workspace/file")
def app_workspace_file_write(app_key:str,payload:WorkspaceWriteRequest)->dict:
    return _call(homeserver_app_workspace.write_file,app_key,payload.path,payload.content)


@router.delete("/{app_key}/workspace/file")
def app_workspace_file_delete(app_key:str,path:str=Query(min_length=1,max_length=1000))->dict:
    return _call(homeserver_app_workspace.delete_path,app_key,path)


@router.post("/{app_key}/workspace/rename")
def app_workspace_rename(app_key:str,payload:WorkspaceRenameRequest)->dict:
    return _call(homeserver_app_workspace.rename_path,app_key,payload.path,payload.new_path)


@router.post("/{app_key}/workspace/validate")
def app_workspace_validate(app_key:str)->dict:
    return _call(homeserver_app_workspace.validate_project,app_key)


@router.get("/{app_key}/source")
def app_source_status(app_key:str)->dict:
    return {"source":_call(homeserver_app_sources.source_status,app_key)}


@router.get("/{app_key}/source/history")
def app_source_history(app_key:str,limit:int=Query(default=50,ge=1,le=200))->dict:
    return _call(homeserver_app_sources.source_history,app_key,limit)


@router.post("/{app_key}/source/refresh")
def app_source_refresh(app_key:str,payload:SourceRefreshRequest)->dict:
    return {"source":_call(homeserver_app_sources.refresh_git_ref,app_key,payload.ref)}


@router.post("/{app_key}/source/detach")
def app_source_detach(app_key:str,payload:SourceDetachRequest)->dict:
    return {"source":_call(homeserver_app_sources.detach,app_key,confirmed=payload.confirmed)}


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


@router.get("/admin/sample-data")
def app_sample_data_settings()->dict:
    return {"sample_data":homeserver_app_sample_data.settings_status()}


@router.put("/admin/sample-data")
def app_sample_data_settings_update(payload:SampleDataSettingsRequest)->dict:
    return {"sample_data":homeserver_app_sample_data.set_enabled(payload.enabled)}


@router.get("/{app_key}/sample-data")
def app_sample_data(app_key:str)->dict:
    return {"sample_data":_call(homeserver_app_sample_data.load_app_sample_data,app_key)}


@router.post("/{app_key}/archive")
def archive_user_app(app_key:str)->dict:
    return {"app":_call(homeserver_apps.archive_user_app,app_key)}


@router.post("/{app_key}/resume")
def resume_user_app(app_key:str)->dict:
    return {"app":_call(homeserver_apps.resume_user_app,app_key)}


@router.get("/{app_key}/releases")
def app_release_history(app_key:str)->dict:
    return homeserver_app_releases.list_releases(app_key)


@router.post("/{app_key}/releases/{release_id}/promote")
def app_release_promote(app_key:str,release_id:str)->dict:
    return _call(homeserver_app_releases.promote,app_key,release_id)


@router.post("/{app_key}/rollback")
def app_release_rollback(app_key:str)->dict:
    return _call(homeserver_app_releases.rollback,app_key)


@router.post("/{app_key}/recover")
def app_release_recover(app_key:str)->dict:
    return _call(homeserver_app_releases.recover,app_key)


@router.post("/agent-actions/{request_id}/approve")
def approve_app_agent_action(request_id:str)->dict:
    try:
        return {"request":homeserver_app_approvals.approve(request_id)}
    except homeserver_app_approvals.AppApprovalStoreError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post("/agent-actions/{request_id}/deny")
def deny_app_agent_action(request_id:str)->dict:
    try:
        return {"request":homeserver_app_approvals.deny(request_id)}
    except homeserver_app_approvals.AppApprovalStoreError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc

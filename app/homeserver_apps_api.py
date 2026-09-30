from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from .services import homeserver_app_agent, homeserver_app_control, homeserver_app_distribution, homeserver_app_manager, homeserver_app_packages, homeserver_app_platform, homeserver_app_prebuilt, homeserver_app_releases, homeserver_app_resources, homeserver_app_runtime, homeserver_app_sample_data, homeserver_app_security, homeserver_app_sources, homeserver_app_workspace, homeserver_apps, homeserver_download_manager, homeserver_media_processor, homeserver_media_server, homeserver_media_tools, homeserver_music_server, homeserver_photo_library, homeserver_video_editor

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


class MediaMappedRootRequest(BaseModel):
    path:str=Field(min_length=1,max_length=2000)
    label:str=Field(default="",max_length=120)
    computer_name:str=Field(default="",max_length=120)
    source_hint:str=Field(default="",max_length=240)
    source_kind:str=Field(default="computer_folder",pattern="^(computer_folder|network_share|local_folder)$")


class MediaProcessRequest(BaseModel):
    operation:str=Field(pattern="^(thumbnail|proxy|video\\.convert|audio\\.convert|image\\.convert)$")
    preset:str=Field(default="default",max_length=80)
    output_format:str=Field(default="",max_length=20)
    priority:int=Field(default=0,ge=-100,le=100)


class MediaProcessorCreateRequest(BaseModel):
    media_id:str=Field(min_length=1,max_length=100)
    operation:str=Field(pattern="^(thumbnail|proxy|video\\.convert|audio\\.convert|image\\.convert)$")
    preset:str=Field(default="default",max_length=80)
    output_format:str=Field(default="",max_length=20)
    priority:int=Field(default=0,ge=-100,le=100)


class MediaProcessorSettingsRequest(BaseModel):
    values:dict=Field(default_factory=dict)


class DownloadCreateRequest(BaseModel):
    url:str=Field(min_length=1,max_length=4096)
    destination_id:str=Field(default="app-storage",max_length=80)
    filename:str=Field(default="",max_length=240)
    priority:int=Field(default=0,ge=-100,le=100)
    checksum_algorithm:str=Field(default="",pattern="^(|sha256|sha512)$")
    checksum_expected:str=Field(default="",max_length=128)
    max_retries:int=Field(default=3,ge=0,le=10)
    scheduled_at:int|None=None


class DownloadPriorityRequest(BaseModel):
    priority:int=Field(ge=-100,le=100)


class DownloadDestinationRequest(BaseModel):
    path:str=Field(min_length=1,max_length=2000)
    label:str=Field(default="",max_length=120)
    destination_kind:str=Field(default="mapped_folder",pattern="^(mapped_folder|network_share|local_folder)$")


class DownloadSettingsRequest(BaseModel):
    values:dict=Field(default_factory=dict)


class PhotoAlbumRequest(BaseModel):
    name:str=Field(min_length=1,max_length=160)


class PhotoMediaRequest(BaseModel):
    media_id:str=Field(min_length=1,max_length=100)


class PhotoFavoriteRequest(BaseModel):
    enabled:bool=True


class PhotoTagsRequest(BaseModel):
    tags:list[str]=Field(default_factory=list,max_length=50)


class PhotoPersonRequest(BaseModel):
    name:str=Field(min_length=1,max_length=160)


class PhotoPersonAssignRequest(BaseModel):
    media_id:str=Field(min_length=1,max_length=100)
    enabled:bool=True


class MusicPlaylistRequest(BaseModel):
    name:str=Field(min_length=1,max_length=160)


class MusicMediaRequest(BaseModel):
    media_id:str=Field(min_length=1,max_length=100)


class MusicFavoriteRequest(BaseModel):
    enabled:bool=True


class MusicPlaybackRequest(BaseModel):
    command:str=Field(pattern="^(play|pause|stop)$")
    media_id:str=Field(default="",max_length=100)
    position_seconds:float=Field(default=0,ge=0)


class MediaPlaybackRequest(BaseModel):
    position_seconds:float=Field(default=0,ge=0)
    duration_seconds:float=Field(default=0,ge=0)
    completed:bool=False


class VideoProjectRequest(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    width:int=Field(default=1920,ge=320,le=7680)
    height:int=Field(default=1080,ge=240,le=4320)
    fps:float=Field(default=30,ge=1,le=240)


class VideoTrackRequest(BaseModel):
    kind:str=Field(pattern="^(video|audio)$")
    name:str=Field(default="",max_length=120)


class VideoClipRequest(BaseModel):
    track_id:str=Field(min_length=1,max_length=80)
    media_id:str=Field(min_length=1,max_length=100)
    start_seconds:float=Field(default=0,ge=0)


class VideoClipUpdateRequest(BaseModel):
    start_seconds:float|None=Field(default=None,ge=0)
    in_seconds:float|None=Field(default=None,ge=0)
    out_seconds:float|None=Field(default=None,ge=0)
    volume:float|None=Field(default=None,ge=0,le=4)


class VideoRenderRequest(BaseModel):
    preset:str=Field(default="1080p",pattern="^(720p|1080p|4k|source)$")
    format:str=Field(default="mp4",pattern="^(mp4|webm)$")


class AppControlInvokeRequest(BaseModel):
    action:str=Field(min_length=1,max_length=120)
    arguments:dict=Field(default_factory=dict)
    confirmed:bool=False


class AppSettingsUpdateRequest(BaseModel):
    values:dict=Field(default_factory=dict)


class VideoAgentInvokeRequest(BaseModel):
    action:str=Field(min_length=1,max_length=120)
    arguments:dict=Field(default_factory=dict)
    confirmed:bool=False


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
    except homeserver_app_control.AppControlError as exc:
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
    except homeserver_music_server.MusicServerError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_photo_library.PhotoLibraryError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_download_manager.DownloadManagerError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_media_processor.MediaProcessorError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except homeserver_video_editor.VideoEditorError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("")
def list_apps()->dict:
    return homeserver_apps.list_apps()


@router.get("/capability")
def apps_capability()->dict:
    return {**homeserver_apps.public_capability(),"platform":homeserver_app_platform.capability(),"manager":homeserver_app_manager.public_capability(),"control":homeserver_app_control.public_capability(),"media_server":homeserver_media_server.public_capability(),"music_server":homeserver_music_server.public_capability(),"photo_library":homeserver_photo_library.public_capability(),"download_manager":homeserver_download_manager.public_capability(),"media_processor":homeserver_media_processor.capability(),"media_tools":homeserver_media_tools.public_capability(),"video_editor":homeserver_video_editor.public_capability(),"packages":homeserver_app_packages.public_capability(),"security":homeserver_app_security.public_capability(),"resources":homeserver_app_resources.public_capability(),"runtime_services":homeserver_app_runtime.public_capability(),"sample_data":homeserver_app_sample_data.public_capability(),"prebuilt":homeserver_app_prebuilt.public_capability(),"agent":homeserver_app_agent.public_capability(),"releases":homeserver_app_releases.public_capability(),"sources":homeserver_app_sources.public_capability(),"workspace":homeserver_app_workspace.public_capability(),"distribution":homeserver_app_distribution.public_capability()}


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




@router.post("/media-server/mapped-roots")
def media_server_add_mapped_root(payload:MediaMappedRootRequest)->dict:
    return _call(
        homeserver_media_server.add_mapped_root,
        payload.path,
        payload.label,
        computer_name=payload.computer_name,
        source_hint=payload.source_hint,
        source_kind=payload.source_kind,
    )


@router.post("/media-server/roots/{root_id}/check")
def media_server_check_root(root_id:str)->dict:
    return _call(homeserver_media_server.check_root,root_id)

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


@router.post("/media-server/item/{media_id}/process")
def media_server_process(media_id:str,payload:MediaProcessRequest)->dict:
    return _call(
        homeserver_media_server.process_media,media_id,payload.operation,
        payload.preset,payload.output_format,payload.priority
    )


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











@router.get("/media-processor/capability")
def media_processor_capability()->dict:
    return homeserver_media_processor.capability()


@router.get("/media-processor/tools")
def media_processor_tools()->dict:
    return homeserver_media_tools.public_capability()


@router.get("/media-processor/status")
def media_processor_status()->dict:
    return _call(homeserver_media_processor.status)


@router.get("/media-processor/brain-context")
def media_processor_brain_context(limit:int=Query(default=8,ge=1,le=20))->dict:
    return _call(homeserver_media_processor.brain_context,limit)


@router.get("/media-processor/jobs")
def media_processor_jobs(limit:int=Query(default=100,ge=1,le=500))->dict:
    return _call(homeserver_media_processor.list_jobs,limit)


@router.post("/media-processor/jobs")
def media_processor_enqueue(payload:MediaProcessorCreateRequest)->dict:
    return _call(
        homeserver_media_processor.enqueue,payload.media_id,payload.operation,
        payload.preset,payload.output_format,payload.priority
    )


@router.get("/media-processor/jobs/{job_id}")
def media_processor_job(job_id:str)->dict:
    return _call(homeserver_media_processor.get_job,job_id)


@router.delete("/media-processor/jobs/{job_id}")
def media_processor_cancel(job_id:str)->dict:
    return _call(homeserver_media_processor.cancel,job_id)


@router.post("/media-processor/jobs/{job_id}/retry")
def media_processor_retry(job_id:str)->dict:
    return _call(homeserver_media_processor.retry,job_id)


@router.get("/media-processor/derivatives")
def media_processor_derivatives(
    media_id:str=Query(default="",max_length=100),
    limit:int=Query(default=200,ge=1,le=500),
)->dict:
    return _call(homeserver_media_processor.derivatives,media_id,limit)


@router.get("/media-processor/derivatives/{derivative_id}/file")
def media_processor_derivative_file(derivative_id:str):
    path,row=_call(homeserver_media_processor.resolve_derivative,derivative_id)
    from fastapi.responses import FileResponse
    mime={
        "mp4":"video/mp4","webm":"video/webm","mp3":"audio/mpeg",
        "jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"
    }.get(str(row.get("format") or "").lower(),"application/octet-stream")
    return FileResponse(
        path,media_type=mime,filename=None,
        headers={
            "Cache-Control":"private, no-store",
            "X-Content-Type-Options":"nosniff",
            "X-VP3-Derivative-Id":str(row["derivative_id"]),
        },
    )


@router.get("/media-processor/settings")
def media_processor_settings()->dict:
    return _call(homeserver_media_processor.settings)


@router.put("/media-processor/settings")
def media_processor_update_settings(payload:MediaProcessorSettingsRequest)->dict:
    return _call(homeserver_media_processor.update_settings,payload.values)

@router.get("/media-processor/remote")
def media_processor_remote_status()->dict:
    return _call(homeserver_media_processor.remote_status)


@router.post("/media-processor/remote/enable")
def media_processor_remote_enable()->dict:
    return _call(homeserver_media_processor.enable_remote)


@router.post("/media-processor/remote/disable")
def media_processor_remote_disable()->dict:
    return _call(homeserver_media_processor.disable_remote)


@router.get("/download-manager/capability")
def download_manager_capability()->dict:
    return homeserver_download_manager.public_capability()


@router.get("/download-manager/status")
def download_manager_status()->dict:
    return _call(homeserver_download_manager.status)


@router.get("/download-manager/brain-context")
def download_manager_brain_context(limit:int=Query(default=8,ge=1,le=20))->dict:
    return _call(homeserver_download_manager.brain_context,limit)


@router.get("/download-manager/downloads")
def download_manager_list(status:str=Query(default="",max_length=40),limit:int=Query(default=200,ge=1,le=1000))->dict:
    return _call(homeserver_download_manager.list_downloads,status,limit)


@router.post("/download-manager/downloads")
def download_manager_enqueue(payload:DownloadCreateRequest)->dict:
    return _call(
        homeserver_download_manager.enqueue,payload.url,
        destination_id=payload.destination_id,filename=payload.filename,priority=payload.priority,
        checksum_algorithm=payload.checksum_algorithm,checksum_expected=payload.checksum_expected,
        max_retries=payload.max_retries,scheduled_at=payload.scheduled_at,
    )


@router.post("/download-manager/downloads/{download_id}/process")
def download_manager_process(download_id:str,payload:MediaProcessRequest)->dict:
    return _call(
        homeserver_download_manager.handoff_to_processor,download_id,payload.operation,
        payload.preset,payload.output_format,payload.priority
    )


@router.get("/download-manager/downloads/{download_id}")
def download_manager_get(download_id:str)->dict:
    return _call(homeserver_download_manager.get_download,download_id)


@router.post("/download-manager/downloads/{download_id}/pause")
def download_manager_pause(download_id:str)->dict:
    return _call(homeserver_download_manager.pause,download_id)


@router.post("/download-manager/downloads/{download_id}/resume")
def download_manager_resume(download_id:str)->dict:
    return _call(homeserver_download_manager.resume,download_id)


@router.post("/download-manager/downloads/{download_id}/retry")
def download_manager_retry(download_id:str)->dict:
    return _call(homeserver_download_manager.retry,download_id)


@router.put("/download-manager/downloads/{download_id}/priority")
def download_manager_priority(download_id:str,payload:DownloadPriorityRequest)->dict:
    return _call(homeserver_download_manager.set_priority,download_id,payload.priority)


@router.delete("/download-manager/downloads/{download_id}")
def download_manager_cancel(download_id:str)->dict:
    return _call(homeserver_download_manager.cancel,download_id)


@router.delete("/download-manager/history")
def download_manager_clear_history()->dict:
    return _call(homeserver_download_manager.clear_history)


@router.get("/download-manager/destinations")
def download_manager_destinations()->dict:
    return _call(homeserver_download_manager.destinations)


@router.post("/download-manager/destinations")
def download_manager_add_destination(payload:DownloadDestinationRequest)->dict:
    return _call(homeserver_download_manager.add_destination,payload.path,payload.label,payload.destination_kind)


@router.delete("/download-manager/destinations/{destination_id}")
def download_manager_remove_destination(destination_id:str)->dict:
    return _call(homeserver_download_manager.remove_destination,destination_id)


@router.get("/download-manager/settings")
def download_manager_settings()->dict:
    return _call(homeserver_download_manager.settings)


@router.put("/download-manager/settings")
def download_manager_update_settings(payload:DownloadSettingsRequest)->dict:
    return _call(homeserver_download_manager.update_settings,payload.values)


@router.get("/download-manager/remote")
def download_manager_remote_status()->dict:
    return _call(homeserver_download_manager.remote_status)


@router.post("/download-manager/remote/enable")
def download_manager_remote_enable()->dict:
    return _call(homeserver_download_manager.enable_remote)


@router.post("/download-manager/remote/disable")
def download_manager_remote_disable()->dict:
    return _call(homeserver_download_manager.disable_remote)

@router.get("/photo-library/capability")
def photo_library_capability()->dict:
    return homeserver_photo_library.public_capability()


@router.get("/photo-library/status")
def photo_library_status()->dict:
    return _call(homeserver_photo_library.status)


@router.post("/photo-library/sync")
def photo_library_sync()->dict:
    return _call(homeserver_photo_library.sync)


@router.get("/photo-library/photos")
def photo_library_photos(
    q:str=Query(default="",max_length=200),
    folder_album:str=Query(default="",max_length=240),
    tag:str=Query(default="",max_length=80),
    favorites_only:bool=Query(default=False),
    limit:int=Query(default=200,ge=1,le=500),
    offset:int=Query(default=0,ge=0,le=1_000_000),
)->dict:
    return _call(homeserver_photo_library.photos,q,folder_album,tag,favorites_only,limit,offset)


@router.get("/photo-library/folders")
def photo_library_folders()->dict:
    return _call(homeserver_photo_library.folders)


@router.get("/photo-library/timeline")
def photo_library_timeline(limit:int=Query(default=500,ge=1,le=2000))->dict:
    return _call(homeserver_photo_library.timeline,limit)


@router.put("/photo-library/favorites/{media_id}")
def photo_library_favorite(media_id:str,payload:PhotoFavoriteRequest)->dict:
    return _call(homeserver_photo_library.favorite,media_id,payload.enabled)


@router.get("/photo-library/tags")
def photo_library_tags()->dict:
    return _call(homeserver_photo_library.tags)


@router.put("/photo-library/photos/{media_id}/tags")
def photo_library_set_tags(media_id:str,payload:PhotoTagsRequest)->dict:
    return _call(homeserver_photo_library.set_tags,media_id,payload.tags)


@router.get("/photo-library/albums")
def photo_library_albums()->dict:
    return _call(homeserver_photo_library.albums)


@router.post("/photo-library/albums")
def photo_library_create_album(payload:PhotoAlbumRequest)->dict:
    return _call(homeserver_photo_library.create_album,payload.name)


@router.get("/photo-library/albums/{album_id}")
def photo_library_album(album_id:str)->dict:
    return _call(homeserver_photo_library.album,album_id)


@router.post("/photo-library/albums/{album_id}/photos")
def photo_library_album_add(album_id:str,payload:PhotoMediaRequest)->dict:
    return _call(homeserver_photo_library.album_add,album_id,payload.media_id)


@router.delete("/photo-library/albums/{album_id}/photos/{media_id}")
def photo_library_album_remove(album_id:str,media_id:str)->dict:
    return _call(homeserver_photo_library.album_remove,album_id,media_id)


@router.delete("/photo-library/albums/{album_id}")
def photo_library_album_delete(album_id:str)->dict:
    return _call(homeserver_photo_library.delete_album,album_id)


@router.get("/photo-library/people")
def photo_library_people()->dict:
    return _call(homeserver_photo_library.people)


@router.post("/photo-library/people")
def photo_library_create_person(payload:PhotoPersonRequest)->dict:
    return _call(homeserver_photo_library.create_person,payload.name)


@router.put("/photo-library/people/{person_id}/photos")
def photo_library_assign_person(person_id:str,payload:PhotoPersonAssignRequest)->dict:
    return _call(homeserver_photo_library.person_assign,person_id,payload.media_id,payload.enabled)


@router.get("/photo-library/duplicates")
def photo_library_duplicates(limit:int=Query(default=100,ge=1,le=500))->dict:
    return _call(homeserver_photo_library.duplicate_groups,limit)


@router.get("/photo-library/smart-albums")
def photo_library_smart_albums()->dict:
    return _call(homeserver_photo_library.smart_albums)


@router.get("/photo-library/slideshow")
def photo_library_slideshow(
    q:str=Query(default="",max_length=200),
    folder_album:str=Query(default="",max_length=240),
    limit:int=Query(default=200,ge=1,le=500),
)->dict:
    return _call(homeserver_photo_library.slideshow,q,folder_album,limit)

@router.get("/music-server/capability")
def music_server_capability()->dict:
    return homeserver_music_server.public_capability()


@router.get("/music-server/status")
def music_server_status()->dict:
    return _call(homeserver_music_server.status)


@router.post("/music-server/sync")
def music_server_sync()->dict:
    return _call(homeserver_music_server.sync)


@router.get("/music-server/tracks")
def music_server_tracks(
    q:str=Query(default="",max_length=200),
    artist:str=Query(default="",max_length=240),
    album:str=Query(default="",max_length=240),
    limit:int=Query(default=200,ge=1,le=1000),
)->dict:
    return _call(homeserver_music_server.tracks,q,artist,album,limit)


@router.get("/music-server/artists")
def music_server_artists(limit:int=Query(default=500,ge=1,le=1000))->dict:
    return _call(homeserver_music_server.artists,limit)


@router.get("/music-server/albums")
def music_server_albums(artist:str=Query(default="",max_length=240),limit:int=Query(default=500,ge=1,le=1000))->dict:
    return _call(homeserver_music_server.albums,artist,limit)


@router.get("/music-server/favorites")
def music_server_favorites()->dict:
    return _call(homeserver_music_server.favorites)


@router.put("/music-server/favorites/{media_id}")
def music_server_favorite(media_id:str,payload:MusicFavoriteRequest)->dict:
    return _call(homeserver_music_server.favorite,media_id,payload.enabled)


@router.get("/music-server/playlists")
def music_server_playlists()->dict:
    return _call(homeserver_music_server.playlists)


@router.post("/music-server/playlists")
def music_server_create_playlist(payload:MusicPlaylistRequest)->dict:
    return _call(homeserver_music_server.create_playlist,payload.name)


@router.get("/music-server/playlists/{playlist_id}")
def music_server_playlist(playlist_id:str)->dict:
    return _call(homeserver_music_server.playlist,playlist_id)


@router.post("/music-server/playlists/{playlist_id}/tracks")
def music_server_playlist_add(playlist_id:str,payload:MusicMediaRequest)->dict:
    return _call(homeserver_music_server.playlist_add,playlist_id,payload.media_id)


@router.delete("/music-server/playlists/{playlist_id}/tracks/{media_id}")
def music_server_playlist_remove(playlist_id:str,media_id:str)->dict:
    return _call(homeserver_music_server.playlist_remove,playlist_id,media_id)


@router.delete("/music-server/playlists/{playlist_id}")
def music_server_playlist_delete(playlist_id:str)->dict:
    return _call(homeserver_music_server.delete_playlist,playlist_id)


@router.get("/music-server/queue")
def music_server_queue()->dict:
    return _call(homeserver_music_server.queue)


@router.post("/music-server/queue")
def music_server_queue_add(payload:MusicMediaRequest)->dict:
    return _call(homeserver_music_server.queue_add,payload.media_id)


@router.delete("/music-server/queue")
def music_server_queue_clear()->dict:
    return _call(homeserver_music_server.queue_clear)


@router.post("/music-server/playback")
def music_server_playback(payload:MusicPlaybackRequest)->dict:
    return _call(homeserver_music_server.playback,payload.command,payload.media_id,payload.position_seconds)

@router.get("/video-editor/capability")
def video_editor_capability()->dict:
    return homeserver_video_editor.public_capability()


@router.get("/video-editor/status")
def video_editor_status()->dict:
    return _call(homeserver_video_editor.status)


@router.get("/video-editor/projects")
def video_editor_projects(limit:int=Query(default=100,ge=1,le=200))->dict:
    return _call(homeserver_video_editor.list_projects,limit)


@router.post("/video-editor/projects")
def video_editor_create_project(payload:VideoProjectRequest)->dict:
    return _call(homeserver_video_editor.create_project,payload.name,payload.width,payload.height,payload.fps)


@router.get("/video-editor/projects/{project_id}")
def video_editor_project(project_id:str)->dict:
    return _call(homeserver_video_editor.project,project_id)


@router.post("/video-editor/projects/{project_id}/tracks")
def video_editor_add_track(project_id:str,payload:VideoTrackRequest)->dict:
    return _call(homeserver_video_editor.add_track,project_id,payload.kind,payload.name)


@router.post("/video-editor/projects/{project_id}/clips")
def video_editor_add_clip(project_id:str,payload:VideoClipRequest)->dict:
    return _call(homeserver_video_editor.add_clip,project_id,payload.track_id,payload.media_id,payload.start_seconds)


@router.put("/video-editor/projects/{project_id}/clips/{clip_id}")
def video_editor_update_clip(project_id:str,clip_id:str,payload:VideoClipUpdateRequest)->dict:
    return _call(homeserver_video_editor.update_clip,project_id,clip_id,start_seconds=payload.start_seconds,in_seconds=payload.in_seconds,out_seconds=payload.out_seconds,volume=payload.volume)


@router.delete("/video-editor/projects/{project_id}/clips/{clip_id}")
def video_editor_remove_clip(project_id:str,clip_id:str)->dict:
    return _call(homeserver_video_editor.remove_clip,project_id,clip_id)


@router.post("/video-editor/projects/{project_id}/clips/{clip_id}/proxy")
def video_editor_clip_proxy(project_id:str,clip_id:str)->dict:
    return _call(homeserver_video_editor.queue_clip_proxy,project_id,clip_id)


@router.post("/video-editor/projects/{project_id}/render")
def video_editor_render(project_id:str,payload:VideoRenderRequest)->dict:
    return _call(homeserver_video_editor.queue_render,project_id,payload.preset,payload.format)


@router.get("/video-editor/render/{render_id}")
def video_editor_render_status(render_id:str)->dict:
    return _call(homeserver_video_editor.render_status,render_id)


@router.get("/video-editor/agent-actions")
def video_editor_agent_actions()->dict:
    return homeserver_video_editor.agent_actions()


@router.post("/video-editor/agent-invoke")
def video_editor_agent_invoke(payload:VideoAgentInvokeRequest)->dict:
    manifest={row["key"]:row for row in homeserver_video_editor.agent_actions()["actions"]}
    spec=manifest.get(payload.action)
    if spec is None:
        raise HTTPException(status_code=404,detail="Video Editor agent action not found.")
    if bool(spec.get("requires_confirmation")) and not payload.confirmed:
        raise HTTPException(status_code=409,detail="This Video Editor action requires owner confirmation.")
    return _call(homeserver_video_editor.invoke,payload.action,payload.arguments)

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




@router.get("/{app_key}/control")
def app_control_status(app_key:str)->dict:
    return {
        "compatibility":_call(homeserver_app_control.compatibility,app_key),
        "manifest":_call(homeserver_app_control.manifest,app_key),
        "settings":_call(homeserver_app_control.settings,app_key),
        "hosting":_call(homeserver_app_agent.hosting_status,{"app_key":app_key}),
    }


@router.get("/{app_key}/control/actions")
def app_control_actions(app_key:str)->dict:
    return _call(homeserver_app_control.manifest,app_key)


@router.post("/{app_key}/control/invoke")
def app_control_invoke(app_key:str,payload:AppControlInvokeRequest)->dict:
    spec=_call(homeserver_app_control.action_spec,app_key,payload.action)
    if bool(spec.get("requires_confirmation")) and not payload.confirmed:
        raise HTTPException(status_code=409,detail="This app action requires owner confirmation.")
    return _call(homeserver_app_control.invoke,app_key,payload.action,payload.arguments)


@router.get("/{app_key}/settings")
def app_control_settings(app_key:str)->dict:
    return _call(homeserver_app_control.settings,app_key)


@router.put("/{app_key}/settings")
def app_control_settings_update(app_key:str,payload:AppSettingsUpdateRequest)->dict:
    return _call(homeserver_app_control.update_settings,app_key,payload.values)


@router.get("/{app_key}/hosting")
def app_control_hosting(app_key:str)->dict:
    return _call(homeserver_app_agent.hosting_status,{"app_key":app_key})


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

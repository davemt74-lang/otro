from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from ..database import db
from . import (
    homeserver_app_packages,
    homeserver_app_releases,
    homeserver_app_resources,
    homeserver_app_runtime,
    homeserver_app_sdk,
    homeserver_app_security,
    homeserver_app_sources,
    homeserver_apps,
)

CONTRACT="vp3.app.development-workspace.v1"
MAX_TEXT_BYTES=2*1024*1024
MAX_FILES=5000
TEXT_EXTENSIONS={
    ".html",".htm",".css",".js",".mjs",".json",".php",".md",".txt",".sql",
    ".xml",".svg",".yml",".yaml",".toml",".ini",".csv",
}
PROTECTED_PATHS={"vp3-app.json"}
IGNORED_PARTS={".git",".svn","__pycache__"}


class AppWorkspaceError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _app(app_key:str)->dict[str,Any]:
    try:
        app=homeserver_apps.get(app_key)
    except homeserver_apps.HomeServerAppError as exc:
        raise AppWorkspaceError(str(exc),exc.status_code) from exc
    if app["app_class"]!="user" or app["protected_system_app"]:
        raise AppWorkspaceError("Development Workspace is available only for user-created apps.",409)
    if app["source_type"] not in {"user_created","agent_builder"}:
        raise AppWorkspaceError("Detach the external source before editing this app in Development Workspace.",409)
    return app


def _root(app_key:str)->Path:
    _app(app_key)
    root=homeserver_app_sdk.app_root(app_key)
    if not root.is_dir():
        raise AppWorkspaceError("User app project source is unavailable.",404)
    return root.resolve()


def _rel(value:str,*,allow_missing:bool=False)->PurePosixPath:
    raw=str(value or "").strip().replace("\\","/")
    rel=PurePosixPath(raw)
    if not raw or rel.is_absolute() or any(part in {"",".",".."} for part in rel.parts):
        raise AppWorkspaceError("Workspace path is invalid.")
    if any(part in IGNORED_PARTS for part in rel.parts):
        raise AppWorkspaceError("Workspace path is not editable.")
    return rel


def _path(app_key:str,value:str,*,allow_missing:bool=False)->tuple[Path,PurePosixPath]:
    root=_root(app_key)
    rel=_rel(value,allow_missing=allow_missing)
    target=(root/Path(*rel.parts)).resolve()
    if target!=root and root not in target.parents:
        raise AppWorkspaceError("Workspace path escaped the app project.",409)
    if not allow_missing and not target.exists():
        raise AppWorkspaceError("Workspace file was not found.",404)
    return target,rel


def _editable(rel:PurePosixPath)->None:
    if rel.suffix.lower() not in TEXT_EXTENSIONS:
        raise AppWorkspaceError("Development Workspace edits text source files only.",415)


def _record(app_key:str,event_type:str,metadata:dict[str,Any])->None:
    app=homeserver_apps.get(app_key)
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?,?, 'owner','development_workspace',?)""",
            (app["app_id"],event_type,json.dumps(metadata,separators=(",",":"),sort_keys=True)),
        )


def list_files(app_key:str)->dict[str,Any]:
    root=_root(app_key)
    rows=[]
    count=0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        rel=path.relative_to(root)
        if any(part in IGNORED_PARTS for part in rel.parts):
            continue
        count+=1
        if count>MAX_FILES:
            raise AppWorkspaceError("App project contains too many workspace entries.",413)
        rows.append({
            "path":rel.as_posix(),
            "type":"directory" if path.is_dir() else "file",
            "bytes":0 if path.is_dir() else path.stat().st_size,
            "editable":bool(path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS),
            "protected":rel.as_posix() in PROTECTED_PATHS,
        })
    return {"contract":CONTRACT,"app_key":app_key,"files":rows,"count":len(rows)}


def read_file(app_key:str,path:str)->dict[str,Any]:
    target,rel=_path(app_key,path)
    if not target.is_file() or target.is_symlink():
        raise AppWorkspaceError("Workspace path is not a readable file.",404)
    _editable(rel)
    size=target.stat().st_size
    if size>MAX_TEXT_BYTES:
        raise AppWorkspaceError("Workspace file exceeds the editor size limit.",413)
    try:
        content=target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AppWorkspaceError("Workspace file is not UTF-8 text.",415) from exc
    return {
        "contract":CONTRACT,
        "app_key":app_key,
        "path":rel.as_posix(),
        "content":content,
        "bytes":size,
        "protected":rel.as_posix() in PROTECTED_PATHS,
    }


def _validate_manifest_identity(app_key:str,content:str)->None:
    try:
        value=json.loads(content)
    except json.JSONDecodeError as exc:
        raise AppWorkspaceError("vp3-app.json must contain valid JSON.") from exc
    if not isinstance(value,dict):
        raise AppWorkspaceError("vp3-app.json must contain an object.")
    if str(value.get("contract") or "")!="vp3.app.package.v1":
        raise AppWorkspaceError("vp3-app.json contract cannot be changed.")
    if str(value.get("app_key") or "").strip().lower()!=app_key:
        raise AppWorkspaceError("App identity cannot be changed from Development Workspace.",409)


def write_file(app_key:str,path:str,content:str)->dict[str,Any]:
    _app(app_key)
    target,rel=_path(app_key,path,allow_missing=True)
    _editable(rel)
    data=str(content).encode("utf-8")
    if len(data)>MAX_TEXT_BYTES:
        raise AppWorkspaceError("Workspace file exceeds the editor size limit.",413)
    if rel.as_posix()=="vp3-app.json":
        _validate_manifest_identity(app_key,str(content))
    target.parent.mkdir(parents=True,exist_ok=True)
    previous=target.read_bytes() if target.exists() and target.is_file() else None
    existed=previous is not None
    tmp=target.with_name(target.name+".workspace-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp,target)
        # Validate the entire canonical package after every source mutation.
        homeserver_app_packages.build_project_package(app_key)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        if existed:
            target.write_bytes(previous or b"")
        else:
            target.unlink(missing_ok=True)
        if isinstance(exc,AppWorkspaceError):
            raise
        raise AppWorkspaceError(f"Workspace change failed package validation: {exc}",409) from exc
    _record(app_key,"app.workspace.file_written",{"path":rel.as_posix(),"bytes":len(data),"created":not existed})
    return {"contract":CONTRACT,"app_key":app_key,"path":rel.as_posix(),"bytes":len(data),"created":not existed,"validated":True}


def delete_path(app_key:str,path:str)->dict[str,Any]:
    target,rel=_path(app_key,path)
    if rel.as_posix() in PROTECTED_PATHS:
        raise AppWorkspaceError("Core app identity files cannot be deleted.",409)
    if target.is_symlink():
        raise AppWorkspaceError("Symbolic links are not editable.",409)
    if target.is_dir():
        if any(target.iterdir()):
            raise AppWorkspaceError("Only empty workspace folders can be deleted.",409)
        target.rmdir()
    else:
        backup=target.read_bytes()
        target.unlink()
        try:
            homeserver_app_packages.build_project_package(app_key)
        except Exception as exc:
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(backup)
            raise AppWorkspaceError(f"Workspace deletion failed package validation: {exc}",409) from exc
    _record(app_key,"app.workspace.path_deleted",{"path":rel.as_posix()})
    return {"contract":CONTRACT,"app_key":app_key,"path":rel.as_posix(),"deleted":True}


def rename_path(app_key:str,path:str,new_path:str)->dict[str,Any]:
    source,source_rel=_path(app_key,path)
    if source_rel.as_posix() in PROTECTED_PATHS:
        raise AppWorkspaceError("Core app identity files cannot be renamed.",409)
    target,target_rel=_path(app_key,new_path,allow_missing=True)
    if target.exists():
        raise AppWorkspaceError("Workspace destination already exists.",409)
    if source.is_symlink():
        raise AppWorkspaceError("Symbolic links are not editable.",409)
    target.parent.mkdir(parents=True,exist_ok=True)
    os.replace(source,target)
    try:
        homeserver_app_packages.build_project_package(app_key)
    except Exception as exc:
        source.parent.mkdir(parents=True,exist_ok=True)
        os.replace(target,source)
        raise AppWorkspaceError(f"Workspace rename failed package validation: {exc}",409) from exc
    _record(app_key,"app.workspace.path_renamed",{"from":source_rel.as_posix(),"to":target_rel.as_posix()})
    return {"contract":CONTRACT,"app_key":app_key,"from":source_rel.as_posix(),"to":target_rel.as_posix(),"renamed":True}


def validate_project(app_key:str)->dict[str,Any]:
    _app(app_key)
    try:
        built=homeserver_app_packages.build_project_package(app_key)
    except homeserver_app_packages.AppPackageError as exc:
        raise AppWorkspaceError(str(exc),exc.status_code) from exc
    validation=dict(built["validation"])
    validation.pop("manifest",None)
    manifest=built["validation"]["manifest"]
    return {
        "contract":CONTRACT,
        "app_key":app_key,
        "valid":True,
        "manifest":manifest,
        "package":{
            "sha256":validation.get("package_sha256"),
            "compressed_bytes":validation.get("compressed_bytes"),
            "expanded_bytes":validation.get("expanded_bytes"),
            "file_count":validation.get("file_count"),
        },
        "permission_delta":homeserver_app_security.permission_delta(app_key,list(manifest.get("permissions") or [])),
    }


def status(app_key:str)->dict[str,Any]:
    app=_app(app_key)
    validation=None
    validation_error=""
    try:
        validation=validate_project(app_key)
    except AppWorkspaceError as exc:
        validation_error=str(exc)
    def safe(call,default=None):
        try:
            return call()
        except Exception:
            return default
    return {
        "contract":CONTRACT,
        "app":app,
        "project":list_files(app_key),
        "validation":validation,
        "validation_error":validation_error,
        "permissions":safe(lambda:homeserver_app_security.permission_status(app_key)),
        "resources":safe(lambda:homeserver_app_resources.resource_status(app_key)),
        "runtime":safe(lambda:homeserver_app_runtime.runtime_status(app_key)),
        "releases":safe(lambda:homeserver_app_releases.list_releases(app_key)),
        "source":safe(lambda:homeserver_app_sources.source_status(app_key)),
        "preview_url":f"/api/v1/control/homeserver-apps/{app_key}/preview/",
        "build_install_url":f"/api/v1/control/homeserver-apps/{app_key}/build-install",
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "user_app_workspace":True,
        "text_source_editor":True,
        "safe_path_boundary":True,
        "app_identity_protected":True,
        "package_validation_on_write":True,
        "persistent_data_separate":True,
        "build_install_uses_canonical_release_engine":True,
        "preview_uses_canonical_runtime":True,
        "release_history_uses_canonical_release_engine":True,
        "permissions_use_section8_governance":True,
        "data_uses_section7_recovery":True,
        "external_source_requires_detach_before_edit":True,
    }

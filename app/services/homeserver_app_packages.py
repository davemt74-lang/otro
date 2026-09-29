from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import settings
from ..database import db
from . import homeserver_app_resources, homeserver_app_runtime, homeserver_app_sdk, homeserver_app_security, homeserver_apps

CONTRACT="vp3.app.package.v1"
RUNTIME_CONTRACT="vp3.app.runtime-install.v1"
MAX_PACKAGE_BYTES=64*1024*1024
MAX_UNCOMPRESSED_BYTES=256*1024*1024
MAX_FILES=5000
_ALLOWED_RUNTIMES={"static","php"}
_ALLOWED_KEYS={
    "contract","app_key","name","version","runtime","entrypoint","sdk_version",
    "permissions","settings_schema","database_migrations","agent_actions","routes","jobs","events",
}


class AppPackageError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe_rel(value:str)->PurePosixPath:
    rel=PurePosixPath(str(value or "").replace("\\","/"))
    if not rel.parts or rel.is_absolute() or any(part in {"",".",".."} for part in rel.parts):
        raise AppPackageError("Package contains an unsafe path.")
    if ":" in rel.parts[0]:
        raise AppPackageError("Package contains an unsafe path.")
    return rel


def runtime_root()->Path:
    root=settings.data_dir/"app-runtime"
    (root/".staging").mkdir(parents=True,exist_ok=True)
    return root


def releases_root(app_key:str)->Path:
    app=homeserver_apps.get(app_key)
    root=(runtime_root()/app["app_key"]/"releases")
    root.mkdir(parents=True,exist_ok=True)
    return root


def _manifest_from_archive(archive:zipfile.ZipFile)->dict[str,Any]:
    try:
        raw=archive.read("vp3-app.json")
    except KeyError as exc:
        raise AppPackageError("App package is missing vp3-app.json.") from exc
    try:
        manifest=json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppPackageError("App manifest is invalid JSON.") from exc
    if not isinstance(manifest,dict):
        raise AppPackageError("App manifest must be an object.")
    unknown=set(manifest)-_ALLOWED_KEYS
    if unknown:
        raise AppPackageError("App manifest contains unsupported fields: "+", ".join(sorted(unknown))+".")
    required={"contract","app_key","name","version","runtime","entrypoint","permissions"}
    missing=sorted(key for key in required if key not in manifest)
    if missing:
        raise AppPackageError("App manifest is missing required fields: "+", ".join(missing)+".")
    if manifest.get("contract")!=CONTRACT:
        raise AppPackageError("App manifest contract is unsupported.")
    key=str(manifest.get("app_key") or "").strip().lower()
    if not homeserver_apps._KEY_RE.fullmatch(key):
        raise AppPackageError("App manifest app_key is invalid.")
    name=str(manifest.get("name") or "").strip()
    if not name or len(name)>160:
        raise AppPackageError("App manifest name is invalid.")
    version=str(manifest.get("version") or "").strip()
    if not version or len(version)>80:
        raise AppPackageError("App manifest version is invalid.")
    runtime=str(manifest.get("runtime") or "").strip().lower()
    if runtime not in _ALLOWED_RUNTIMES:
        raise AppPackageError("App runtime must be static or php.")
    entry=_safe_rel(str(manifest.get("entrypoint") or ""))
    if runtime=="static" and entry.suffix.lower() not in {".html",".htm"}:
        raise AppPackageError("Static app entrypoint must be an HTML file.")
    if runtime=="php" and entry.suffix.lower()!=".php":
        raise AppPackageError("PHP app entrypoint must be a PHP file.")
    permissions=manifest.get("permissions")
    if not isinstance(permissions,list) or any(not isinstance(x,str) or not x.strip() or len(x)>120 for x in permissions):
        raise AppPackageError("App permissions must be a list of non-empty strings.")
    if len(set(permissions))!=len(permissions):
        raise AppPackageError("App permissions may not contain duplicates.")
    try:
        manifest["permissions"]=homeserver_app_security.normalize_declared_permissions(permissions)
    except homeserver_app_security.AppSecurityError as exc:
        raise AppPackageError(str(exc)) from exc
    sdk_version=str(manifest.get("sdk_version") or "").strip()
    if sdk_version and sdk_version!="1.0":
        raise AppPackageError("App SDK version is not supported.")
    routes=manifest.get("routes",{})
    if routes is not None:
        if not isinstance(routes,dict) or any(k not in {"local","private_remote","public"} for k in routes):
            raise AppPackageError("App routes are invalid.")
        if any(not isinstance(v,bool) for v in routes.values()):
            raise AppPackageError("App route flags must be booleans.")
    for field in ("settings_schema","database_migrations","agent_actions","jobs","events","sample_data"):
        if manifest.get(field):
            _safe_rel(str(manifest[field]))
    manifest["app_key"]=key
    manifest["runtime"]=runtime
    manifest["entrypoint"]=entry.as_posix()
    manifest["version"]=version
    return manifest


def validate_package(package:bytes,*,expected_app_key:str|None=None)->dict[str,Any]:
    if not package:
        raise AppPackageError("App package is empty.")
    if len(package)>MAX_PACKAGE_BYTES:
        raise AppPackageError("App package exceeds the compressed size limit.",413)
    digest=hashlib.sha256(package).hexdigest()
    try:
        archive=zipfile.ZipFile(io.BytesIO(package),"r")
    except zipfile.BadZipFile as exc:
        raise AppPackageError("App package is not a valid ZIP archive.") from exc
    seen:set[str]=set()
    expanded=0
    files=0
    try:
        for info in archive.infolist():
            rel=_safe_rel(info.filename)
            normalized=rel.as_posix()
            if normalized in seen:
                raise AppPackageError("App package contains duplicate paths.")
            seen.add(normalized)
            mode=(info.external_attr>>16)&0o170000
            if mode==0o120000:
                raise AppPackageError("App packages may not contain symbolic links.")
            if mode not in {0,0o040000,0o100000}:
                raise AppPackageError("App package contains an unsupported special file.")
            if info.flag_bits&0x1:
                raise AppPackageError("Encrypted app packages are not supported.")
            if not info.is_dir():
                files+=1
                if files>MAX_FILES:
                    raise AppPackageError("App package contains too many files.",413)
                expanded+=max(0,int(info.file_size))
                if expanded>MAX_UNCOMPRESSED_BYTES:
                    raise AppPackageError("App package exceeds the expanded size limit.",413)
        manifest=_manifest_from_archive(archive)
        if expected_app_key and manifest["app_key"]!=str(expected_app_key).strip().lower():
            raise AppPackageError("App package identity does not match the target app.",409)
        required=[manifest["entrypoint"]]
        for field in ("settings_schema","agent_actions","jobs","events","sample_data"):
            if manifest.get(field):
                required.append(_safe_rel(str(manifest[field])).as_posix())
        for path in required:
            if path not in seen:
                raise AppPackageError(f"App package references missing file: {path}.")
        sample_path=str(manifest.get("sample_data") or "").strip()
        if sample_path:
            try:
                sample=json.loads(archive.read(_safe_rel(sample_path).as_posix()).decode("utf-8"))
            except (KeyError,UnicodeDecodeError,json.JSONDecodeError) as exc:
                raise AppPackageError("App sample data contract is invalid.") from exc
            if not isinstance(sample,dict) or sample.get("contract")!="vp3.app.sample-data.v1":
                raise AppPackageError("App sample data contract must be vp3.app.sample-data.v1.")
            items=sample.get("items",[])
            if not isinstance(items,list) or len(items)>500:
                raise AppPackageError("App sample data items are invalid.")
            for index,item in enumerate(items):
                if not isinstance(item,dict):
                    raise AppPackageError(f"App sample data item {index+1} must be an object.")
                raw=json.dumps(item,separators=(",",":"),sort_keys=True)
                if len(raw.encode("utf-8"))>64*1024:
                    raise AppPackageError("An app sample data item exceeds the size limit.")
        mig=manifest.get("database_migrations")
        if mig:
            prefix=_safe_rel(str(mig)).as_posix().rstrip("/")+"/"
            if not any(path.startswith(prefix) for path in seen):
                # Empty migration directories are allowed by SDK; ZIP tools may omit them.
                pass
        return {
            "contract":CONTRACT,
            "manifest":manifest,
            "package_sha256":digest,
            "compressed_bytes":len(package),
            "expanded_bytes":expanded,
            "file_count":files,
            "valid":True,
        }
    finally:
        archive.close()



def build_project_package(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]!="user":
        raise AppPackageError("Only user apps can be packaged from an SDK project.",409)
    try:
        project=homeserver_app_sdk.app_root(app_key)
    except homeserver_app_sdk.AppSdkError as exc:
        raise AppPackageError(str(exc)) from exc
    if not project.is_dir():
        raise AppPackageError("User app project is missing.",404)
    buffer=io.BytesIO()
    file_count=0
    expanded=0
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(project.rglob("*")):
            if path.is_symlink():
                raise AppPackageError("User app project may not contain symbolic links.")
            if path.is_dir():
                continue
            rel=path.relative_to(project).as_posix()
            _safe_rel(rel)
            if any(part in {"__pycache__",".git",".svn"} for part in Path(rel).parts):
                continue
            size=path.stat().st_size
            file_count+=1
            expanded+=size
            if file_count>MAX_FILES:
                raise AppPackageError("User app project contains too many files.",413)
            if expanded>MAX_UNCOMPRESSED_BYTES:
                raise AppPackageError("User app project exceeds the expanded size limit.",413)
            archive.write(path,rel)
    package=buffer.getvalue()
    validation=validate_package(package,expected_app_key=app_key)
    return {"package":package,"validation":validation}


def install_project(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    built=build_project_package(app_key)
    return install_package(app_key,built["package"],source_type=app["source_type"])

def _write_state(app_key:str,state:dict[str,Any])->None:
    root=runtime_root()/app_key
    root.mkdir(parents=True,exist_ok=True)
    tmp=root/"runtime-state.json.tmp"
    final=root/"runtime-state.json"
    tmp.write_text(json.dumps(state,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,final)


def _read_state(app_key:str)->dict[str,Any]:
    path=runtime_root()/app_key/"runtime-state.json"
    if not path.is_file():
        return {"contract":RUNTIME_CONTRACT,"app_key":app_key,"active_release_id":None,"previous_release_id":None}
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppPackageError("App runtime state is unreadable.",500) from exc
    if not isinstance(value,dict) or value.get("app_key")!=app_key:
        raise AppPackageError("App runtime state is invalid.",500)
    return value


def install_package(app_key:str,package:bytes,*,source_type:str|None=None)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]!="user":
        raise AppPackageError("System apps are managed by the VP3 system app installer.",409)
    validation=validate_package(package,expected_app_key=app_key)
    manifest=validation["manifest"]
    current_state=str(app["lifecycle_state"])
    if current_state not in {"draft","installed","running","degraded","stopped","failed"}:
        raise AppPackageError("App is busy with another lifecycle operation.",409)
    transition_target="updating" if current_state in {"installed","running","degraded","stopped"} else "installing"
    transitioned=False
    root=releases_root(app_key)
    release_id="apprel_"+uuid.uuid4().hex[:24]
    staging=Path(tempfile.mkdtemp(prefix=".staging-",dir=root))
    final=root/release_id
    archive=zipfile.ZipFile(io.BytesIO(package),"r")
    try:
        content=staging/"content"
        content.mkdir()
        for info in archive.infolist():
            rel=_safe_rel(info.filename)
            target=(content/Path(*rel.parts)).resolve()
            base=content.resolve()
            if target!=base and base not in target.parents:
                raise AppPackageError("App package escaped its staging root.")
            if info.is_dir():
                target.mkdir(parents=True,exist_ok=True)
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info,"r") as source,target.open("wb") as destination:
                shutil.copyfileobj(source,destination,1024*1024)
        entry=(content/Path(*_safe_rel(manifest["entrypoint"]).parts)).resolve()
        if not entry.is_file():
            raise AppPackageError("App entrypoint was not extracted.")
        try:
            homeserver_app_runtime.validate_release_contracts(app_key,content)
        except homeserver_app_runtime.AppRuntimeError as exc:
            raise AppPackageError(str(exc),exc.status_code) from exc
        homeserver_apps.transition(app_key,transition_target,metadata={"version":manifest["version"],"package_sha256":validation["package_sha256"]})
        transitioned=True
        release={
            "contract":"vp3.app.release.v1",
            "release_id":release_id,
            "app_key":app_key,
            "version":manifest["version"],
            "runtime":manifest["runtime"],
            "entrypoint":manifest["entrypoint"],
            "package_sha256":validation["package_sha256"],
            "expanded_bytes":validation["expanded_bytes"],
            "file_count":validation["file_count"],
            "source_type":source_type or app["source_type"],
            "sdk_version":str(manifest.get("sdk_version") or ""),
        }
        (staging/"release.json").write_text(json.dumps(release,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        os.replace(staging,final)
        previous=_read_state(app_key)
        state={
            "contract":RUNTIME_CONTRACT,
            "app_key":app_key,
            "active_release_id":release_id,
            "previous_release_id":previous.get("active_release_id"),
        }
        _write_state(app_key,state)
        metadata=dict(app.get("metadata") or {})
        metadata.update({
            "runtime":manifest["runtime"],
            "entrypoint":manifest["entrypoint"],
            "package_sha256":validation["package_sha256"],
            "active_release_id":release_id,
            "previous_release_id":state["previous_release_id"],
            "permissions":list(manifest.get("permissions") or []),
            "routes":dict(manifest.get("routes") or {}),
            "sdk_version":str(manifest.get("sdk_version") or ""),
        })
        with db() as connection:
            connection.execute(
                """UPDATE homeserver_apps SET installed_version=?,desired_version=?,source_type=?,
                   lifecycle_state='running',metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_key=?""",
                (
                    manifest["version"],manifest["version"],source_type or app["source_type"],
                    json.dumps(metadata,separators=(",",":"),sort_keys=True),app_key,
                ),
            )
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,from_state,to_state,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.package.installed', ?, 'running', 'owner', 'local_owner', ?)""",
                (
                    app["app_id"],transition_target,
                    json.dumps({"release_id":release_id,"version":manifest["version"],"package_sha256":validation["package_sha256"]},separators=(",",":"),sort_keys=True),
                ),
            )
        homeserver_app_security.sync_declared_permissions(app_key,list(manifest.get("permissions") or []))
        homeserver_app_resources.resource_status(app_key)
        homeserver_app_runtime.sync_release(app_key,final/"content")
        homeserver_app_resources.enforce_sqlite_quota(app_key)
        result=dict(release)
        result["active"]=True
        result["previous_release_id"]=state["previous_release_id"]
        return result
    except Exception as exc:
        shutil.rmtree(staging,ignore_errors=True)
        if transitioned:
            try:
                homeserver_apps.transition(app_key,"failed",metadata={"reason":str(exc)[:500]})
            except Exception:
                pass
        raise
    finally:
        archive.close()


def runtime_status(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    state=_read_state(app_key)
    active=state.get("active_release_id")
    release=None
    if active:
        path=releases_root(app_key)/str(active)/"release.json"
        if path.is_file():
            release=json.loads(path.read_text(encoding="utf-8"))
    return {
        "contract":RUNTIME_CONTRACT,
        "app_key":app_key,
        "lifecycle_state":app["lifecycle_state"],
        "installed_version":app["installed_version"],
        "active_release_id":active,
        "previous_release_id":state.get("previous_release_id"),
        "active_release":release,
    }


def active_content_root(app_key:str)->Path:
    status=runtime_status(app_key)
    release=status.get("active_release")
    if not release:
        raise AppPackageError("App has no active runtime release.",409)
    root=(releases_root(app_key)/release["release_id"]/"content").resolve()
    base=releases_root(app_key).resolve()
    if base not in root.parents or not root.is_dir():
        raise AppPackageError("Active app release is unavailable.",500)
    return root


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "runtime_contract":RUNTIME_CONTRACT,
        "zip_packages":True,
        "manifest_validation":True,
        "identity_binding":True,
        "atomic_release_activation":True,
        "package_sha256":True,
        "path_traversal_protection":True,
        "symbolic_links":False,
        "encrypted_zip":False,
        "max_package_bytes":MAX_PACKAGE_BYTES,
        "max_uncompressed_bytes":MAX_UNCOMPRESSED_BYTES,
        "max_files":MAX_FILES,
        "supported_runtimes":sorted(_ALLOWED_RUNTIMES),
        "system_app_installer":"separate_vp3_managed_path",
    }

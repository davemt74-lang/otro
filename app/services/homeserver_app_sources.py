from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..config import settings
from ..database import db
from . import homeserver_app_packages, homeserver_apps

CONTRACT="vp3.app.sources.v1"
GIT_TIMEOUT_SECONDS=45
MAX_GIT_REF=160
_ALLOWED_GIT_HOSTS={"github.com","gitlab.com","bitbucket.org"}


class AppSourceError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _root()->Path:
    root=settings.data_dir/"app-sources"
    (root/".staging").mkdir(parents=True,exist_ok=True)
    (root/"packages").mkdir(parents=True,exist_ok=True)
    return root


def _safe_public_url(value:str)->str:
    raw=str(value or "").strip()
    if not raw or len(raw)>1000:
        raise AppSourceError("Git repository URL is required and must be at most 1000 characters.")
    parsed=urlsplit(raw)
    if parsed.scheme.lower()!="https" or not parsed.hostname:
        raise AppSourceError("Git repository URL must use HTTPS.")
    if parsed.username or parsed.password:
        raise AppSourceError("Git repository URL may not contain embedded credentials.")
    host=parsed.hostname.lower().rstrip(".")
    if host in {"localhost","localhost.localdomain"} or host.endswith(".local"):
        raise AppSourceError("Git repository host is not allowed.")
    if host not in _ALLOWED_GIT_HOSTS:
        raise AppSourceError("Git repository host is not supported. Use GitHub, GitLab, or Bitbucket.")
    try:
        ip=ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise AppSourceError("Git repository host must be publicly routable.")
    except ValueError:
        pass
    clean_path=parsed.path.rstrip("/")
    if not clean_path or clean_path=="/":
        raise AppSourceError("Git repository path is required.")
    return urlunsplit(("https",parsed.netloc,clean_path,"",""))


def _git_ref(value:str)->str:
    ref=str(value or "HEAD").strip()
    if not ref or len(ref)>MAX_GIT_REF or any(ch in ref for ch in "\r\n\0 "):
        raise AppSourceError("Git ref is invalid.")
    if ref.startswith("-"):
        raise AppSourceError("Git ref is invalid.")
    return ref


def _json(raw:str|None)->dict[str,Any]:
    try:
        value=json.loads(raw or "{}")
    except (TypeError,json.JSONDecodeError):
        return {}
    return value if isinstance(value,dict) else {}


def _record(source_id:str,app_key:str,event_type:str,metadata:dict[str,Any]|None=None)->None:
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_source_events(source_id,app_key,event_type,metadata_json)
               VALUES (?,?,?,?)""",
            (source_id,app_key,event_type,json.dumps(metadata or {},separators=(",",":"),sort_keys=True)),
        )


def _public(row)->dict[str,Any]:  # noqa: ANN001
    item=dict(row)
    item["manifest"]=_json(item.pop("manifest_json","{}"))
    item["validation"]=_json(item.pop("validation_json","{}"))
    item.pop("cache_path",None)
    return item


def _persist(*,source_type:str,source_ref:str,source_revision:str,package:bytes,validation:dict[str,Any])->dict[str,Any]:
    manifest=dict(validation["manifest"])
    app_key=str(manifest["app_key"])
    source_id="appsrc_"+uuid.uuid4().hex
    root=_root()
    final=root/"packages"/f"{source_id}.zip"
    tmp=root/".staging"/f"{source_id}.zip.tmp"
    tmp.write_bytes(package)
    os.replace(tmp,final)
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_sources(
               source_id,app_key,source_type,source_ref,source_revision,package_sha256,
               manifest_json,validation_json,status,cache_path
            ) VALUES (?,?,?,?,?,?,?,?, 'inspected', ?)""",
            (
                source_id,app_key,source_type,source_ref[:1000],source_revision[:160],
                validation["package_sha256"],
                json.dumps(manifest,separators=(",",":"),sort_keys=True),
                json.dumps({k:v for k,v in validation.items() if k!="manifest"},separators=(",",":"),sort_keys=True),
                str(final),
            ),
        )
    _record(source_id,app_key,"source.inspected",{
        "source_type":source_type,
        "source_revision":source_revision,
        "package_sha256":validation["package_sha256"],
        "version":manifest.get("version",""),
    })
    return get_source(source_id)


def inspect_zip(package:bytes,filename:str="package.zip")->dict[str,Any]:
    try:
        validation=homeserver_app_packages.validate_package(package)
    except homeserver_app_packages.AppPackageError as exc:
        raise AppSourceError(str(exc),exc.status_code) from exc
    safe_name=Path(str(filename or "package.zip")).name[:240] or "package.zip"
    return _persist(
        source_type="zip",
        source_ref=safe_name,
        source_revision="",
        package=package,
        validation=validation,
    )


def _run_git(args:list[str],cwd:Path)->str:
    env=os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT":"0",
        "GIT_ASKPASS":"/bin/false",
        "GIT_CONFIG_NOSYSTEM":"1",
    })
    try:
        completed=subprocess.run(
            ["git","-c","http.followRedirects=false","-c","credential.helper=",*args],cwd=str(cwd),env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            timeout=GIT_TIMEOUT_SECONDS,check=False,
        )
    except FileNotFoundError as exc:
        raise AppSourceError("Git is not installed on this HomeServer.",503) from exc
    except subprocess.TimeoutExpired as exc:
        raise AppSourceError("Git source inspection timed out.",504) from exc
    if completed.returncode!=0:
        error=(completed.stderr or completed.stdout or "Git command failed.").strip().splitlines()[-1][:500]
        raise AppSourceError(f"Git source inspection failed: {error}",422)
    return completed.stdout.strip()


def inspect_git(repo_url:str,ref:str="HEAD")->dict[str,Any]:
    clean_url=_safe_public_url(repo_url)
    clean_ref=_git_ref(ref)
    staging=Path(tempfile.mkdtemp(prefix="git-",dir=_root()/".staging"))
    repo=staging/"repo"
    archive=staging/"source.zip"
    repo.mkdir()
    try:
        _run_git(["init","--quiet"],repo)
        _run_git(["remote","add","origin",clean_url],repo)
        _run_git(["fetch","--quiet","--depth=1","origin",clean_ref],repo)
        commit=_run_git(["rev-parse","--verify","FETCH_HEAD^{commit}"],repo).lower()
        if len(commit)!=40 or any(ch not in "0123456789abcdef" for ch in commit):
            raise AppSourceError("Git source did not resolve to an exact commit.",422)
        _run_git(["archive","--format=zip",f"--output={archive}","FETCH_HEAD"],repo)
        package=archive.read_bytes()
        try:
            validation=homeserver_app_packages.validate_package(package)
            validation["git_requested_ref"]=clean_ref
        except homeserver_app_packages.AppPackageError as exc:
            raise AppSourceError(str(exc),exc.status_code) from exc
        return _persist(
            source_type="git",
            source_ref=clean_url,
            source_revision=commit,
            package=package,
            validation=validation,
        )
    finally:
        shutil.rmtree(staging,ignore_errors=True)


def get_source(source_id:str)->dict[str,Any]:
    with db() as connection:
        row=connection.execute("SELECT * FROM homeserver_app_sources WHERE source_id=?",(str(source_id or ""),)).fetchone()
    if row is None:
        raise AppSourceError("App source was not found.",404)
    return _public(row)


def source_history(app_key:str,limit:int=50)->dict[str,Any]:
    key=str(app_key or "").strip().lower()
    with db() as connection:
        rows=connection.execute(
            """SELECT source_id,app_key,source_type,source_ref,source_revision,package_sha256,
                      manifest_json,validation_json,status,created_at,updated_at
               FROM homeserver_app_sources WHERE app_key=? ORDER BY created_at DESC,rowid DESC LIMIT ?""",
            (key,max(1,min(200,int(limit)))),
        ).fetchall()
    return {"contract":CONTRACT,"app_key":key,"sources":[_public(row) for row in rows],"count":len(rows)}


def source_status(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    history=source_history(app["app_key"],20)
    metadata=dict(app.get("metadata") or {})
    attached_id=str(metadata.get("source_id") or "")
    attached=next((item for item in history["sources"] if item.get("source_id")==attached_id),None) if attached_id else None
    candidate=next((item for item in history["sources"] if item.get("status")=="inspected"),None)
    current=candidate or attached
    installed_hash=str(metadata.get("package_sha256") or "")
    return {
        "contract":CONTRACT,
        "app_key":app["app_key"],
        "source_type":app["source_type"],
        "source_ref":app.get("source_ref") or "",
        "installed_package_sha256":installed_hash,
        "attached_source_id":attached_id or None,
        "current":current,
        "update_available":bool(candidate and candidate["package_sha256"]!=installed_hash),
        "history":history["sources"],
    }


def install_source(source_id:str,*,approved:bool=False)->dict[str,Any]:
    if approved is not True:
        raise AppSourceError("Owner approval is required before installing an inspected app source.",409)
    source=get_source(source_id)
    if source["status"]=="detached":
        raise AppSourceError("Detached app source cannot be installed.",409)
    app_key=str(source["app_key"])
    manifest=dict(source["manifest"])
    cache_path=None
    with db() as connection:
        row=connection.execute("SELECT cache_path FROM homeserver_app_sources WHERE source_id=?",(source_id,)).fetchone()
        if row is not None:
            cache_path=str(row["cache_path"])
    if not cache_path:
        raise AppSourceError("App source package cache is unavailable.",410)
    package_path=Path(cache_path)
    if not package_path.is_file():
        raise AppSourceError("App source package cache is missing.",410)
    package=package_path.read_bytes()
    actual=hashlib.sha256(package).hexdigest()
    if actual!=source["package_sha256"]:
        raise AppSourceError("App source package checksum changed after inspection.",409)

    try:
        app=homeserver_apps.get(app_key)
        if app["app_class"]!="user" or app["protected_system_app"]:
            raise AppSourceError("VP3 system apps cannot be replaced by imported sources.",409)
    except homeserver_apps.HomeServerAppError as exc:
        if exc.status_code!=404:
            raise
        homeserver_apps.register_user_app(
            app_key,
            str(manifest["name"]),
            source_type=str(source["source_type"]),
            source_ref=str(source["source_ref"]),
            metadata={
                "source_id":source_id,
                "source_revision":source["source_revision"],
                "source_provenance_contract":CONTRACT,
            },
        )

    try:
        release=homeserver_app_packages.install_package(
            app_key,
            package,
            source_type=str(source["source_type"]),
            source_provenance={
                "source_id":source_id,
                "source_ref":source["source_ref"],
                "source_revision":source["source_revision"],
            },
        )
    except (homeserver_app_packages.AppPackageError,homeserver_apps.HomeServerAppError) as exc:
        with db() as connection:
            connection.execute(
                "UPDATE homeserver_app_sources SET status='failed',updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
                (source_id,),
            )
        _record(source_id,app_key,"source.install_failed",{"error":str(exc)[:500]})
        raise AppSourceError(str(exc),getattr(exc,"status_code",400)) from exc

    with db() as connection:
        app_row=connection.execute("SELECT metadata_json FROM homeserver_apps WHERE app_key=?",(app_key,)).fetchone()
        metadata=_json(app_row["metadata_json"] if app_row else "{}")
        metadata.update({
            "source_id":source_id,
            "source_revision":source["source_revision"],
            "source_provenance_contract":CONTRACT,
        })
        connection.execute(
            """UPDATE homeserver_apps SET source_type=?,source_ref=?,metadata_json=?,updated_at=CURRENT_TIMESTAMP
               WHERE app_key=?""",
            (
                source["source_type"],source["source_ref"],
                json.dumps(metadata,separators=(",",":"),sort_keys=True),app_key,
            ),
        )
        connection.execute(
            "UPDATE homeserver_app_sources SET status='installed',updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
            (source_id,),
        )
    _record(source_id,app_key,"source.installed",{
        "release_id":release.get("release_id"),
        "version":release.get("version"),
        "package_sha256":source["package_sha256"],
        "source_revision":source["source_revision"],
    })
    return {"contract":CONTRACT,"source":get_source(source_id),"release":release,"app":homeserver_apps.get(app_key)}


def refresh_git(app_key:str)->dict[str,Any]:
    status=source_status(app_key)
    current=status.get("current")
    if not current or current.get("source_type")!="git":
        raise AppSourceError("This app does not have a Git source to refresh.",409)
    requested=str((current.get("validation") or {}).get("git_requested_ref") or "HEAD")
    return inspect_git(str(current["source_ref"]),requested)


def refresh_git_ref(app_key:str,ref:str="")->dict[str,Any]:
    status=source_status(app_key)
    current=status.get("current")
    if not current or current.get("source_type")!="git":
        raise AppSourceError("This app does not have a Git source to refresh.",409)
    requested=str(ref or (current.get("validation") or {}).get("git_requested_ref") or "HEAD")
    return inspect_git(str(current["source_ref"]),requested)


def detach(app_key:str,*,confirmed:bool=False)->dict[str,Any]:
    if confirmed is not True:
        raise AppSourceError("Owner confirmation is required to detach an app source.",409)
    app=homeserver_apps.get(app_key)
    if app["app_class"]!="user":
        raise AppSourceError("VP3 system app sources cannot be detached.",409)
    status=source_status(app_key)
    current=status.get("current")
    if current:
        with db() as connection:
            connection.execute(
                "UPDATE homeserver_app_sources SET status='detached',updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
                (current["source_id"],),
            )
        _record(current["source_id"],app["app_key"],"source.detached",{})
    metadata=dict(app.get("metadata") or {})
    metadata.pop("source_id",None)
    metadata.pop("source_revision",None)
    with db() as connection:
        connection.execute(
            """UPDATE homeserver_apps SET source_type='user_created',source_ref='',metadata_json=?,updated_at=CURRENT_TIMESTAMP
               WHERE app_key=?""",
            (json.dumps(metadata,separators=(",",":"),sort_keys=True),app["app_key"]),
        )
    return source_status(app["app_key"])


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "zip_import":True,
        "git_import":True,
        "git_https_only":True,
        "git_public_hosts":sorted(_ALLOWED_GIT_HOSTS),
        "exact_git_sha":True,
        "immutable_package_sha256":True,
        "preview_before_install":True,
        "owner_approval_required":True,
        "source_history":True,
        "source_detach":True,
        "protected_system_overwrite":False,
        "path_traversal_protection":True,
    }

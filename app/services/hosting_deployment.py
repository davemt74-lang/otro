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

from ..database import db
from . import hosting_runtime

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_FILES = 5000
MANIFEST_NAME = "vp3-hosting.json"
DEPLOYMENT_CONTRACT = "vp3.hosting.package.v1"


class DeploymentError(hosting_runtime.HostingError):
    pass


def releases_root(site_id: str) -> Path:
    root = hosting_runtime.site_root(site_id) / "releases"
    root.mkdir(parents=True, exist_ok=True)
    hosting_runtime._ensure_no_symlink(root)
    return root


def deployment_state_path(site_id: str) -> Path:
    return hosting_runtime.site_root(site_id) / "deployment-state.json"


def _normalize_member(name: str) -> PurePosixPath:
    raw = str(name or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or path.is_absolute():
        raise DeploymentError("Deployment package contains an absolute path.")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise DeploymentError("Deployment package contains an unsafe path.")
    if ":" in path.parts[0]:
        raise DeploymentError("Deployment package contains a drive-qualified path.")
    return path


def _validate_zip(package: bytes) -> tuple[zipfile.ZipFile, dict[str, Any], str]:
    if not package:
        raise DeploymentError("Deployment package is empty.")
    if len(package) > MAX_PACKAGE_BYTES:
        raise DeploymentError("Deployment package exceeds the compressed size limit.", 413)
    digest = hashlib.sha256(package).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(package), "r")
    except zipfile.BadZipFile as exc:
        raise DeploymentError("Deployment package is not a valid ZIP archive.") from exc

    members = archive.infolist()
    if len(members) > MAX_FILES:
        archive.close()
        raise DeploymentError("Deployment package contains too many files.", 413)

    total = 0
    seen: set[str] = set()
    for info in members:
        path = _normalize_member(info.filename)
        normalized = path.as_posix()
        if normalized in seen:
            archive.close()
            raise DeploymentError("Deployment package contains duplicate paths.")
        seen.add(normalized)
        total += max(0, int(info.file_size))
        if total > MAX_UNCOMPRESSED_BYTES:
            archive.close()
            raise DeploymentError("Deployment package exceeds the expanded size limit.", 413)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            archive.close()
            raise DeploymentError("Deployment package may not contain symbolic links.")

    try:
        raw_manifest = archive.read(MANIFEST_NAME)
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except KeyError as exc:
        archive.close()
        raise DeploymentError(f"Deployment package is missing {MANIFEST_NAME}.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        archive.close()
        raise DeploymentError("Deployment manifest is invalid JSON.") from exc

    if not isinstance(manifest, dict):
        archive.close()
        raise DeploymentError("Deployment manifest must be an object.")
    if manifest.get("contract") != DEPLOYMENT_CONTRACT:
        archive.close()
        raise DeploymentError("Deployment manifest contract is unsupported.")
    runtime = str(manifest.get("runtime") or "").strip().lower()
    if runtime not in {"static", "php"}:
        archive.close()
        raise DeploymentError("Deployment manifest runtime must be static or php.")
    entrypoint = str(manifest.get("entrypoint") or "").strip().replace("\\", "/")
    if not entrypoint:
        archive.close()
        raise DeploymentError("Deployment manifest entrypoint is required.")
    ep = _normalize_member(entrypoint)
    if ep.as_posix() not in seen:
        archive.close()
        raise DeploymentError("Deployment manifest entrypoint is not present in the package.")
    if not ep.parts or ep.parts[0] != "public":
        archive.close()
        raise DeploymentError("Deployment entrypoint must live under public/.")

    return archive, manifest, digest


def _record(site_id: str, event_type: str, state: str, details: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO hosting_runtime_events(site_id,event_type,state,details_json) VALUES (?,?,?,?)",
            (site_id,event_type,state,json.dumps(details,separators=(",",":"),sort_keys=True)),
        )


def _read_state(site_id: str) -> dict[str, Any]:
    path = deployment_state_path(site_id)
    if not path.is_file():
        return {"contract":"vp3.hosting.deployment-state.v1","site_id":site_id,"active_release_id":None,"previous_release_id":None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("Deployment state is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("site_id") != site_id:
        raise DeploymentError("Deployment state is invalid.",500)
    return payload


def _write_state(site_id: str, payload: dict[str, Any]) -> None:
    path = deployment_state_path(site_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(temporary,path)


def _release_manifest(site_id: str, release_id: str) -> dict[str, Any]:
    path = releases_root(site_id) / release_id / "release.json"
    if not path.is_file():
        raise DeploymentError("Hosting release not found.",404)
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise DeploymentError("Hosting release manifest is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("release_id") != release_id or payload.get("site_id") != site_id:
        raise DeploymentError("Hosting release manifest is invalid.",500)
    return payload


def list_releases(site_id: str) -> list[dict[str, Any]]:
    hosting_runtime.get_site(site_id)
    root=releases_root(site_id)
    items=[]
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith("release_"):
            continue
        try:
            items.append(_release_manifest(site_id,child.name))
        except DeploymentError:
            continue
    items.sort(key=lambda item:(str(item.get("created_at") or ""),str(item.get("release_id") or "")),reverse=True)
    state=_read_state(site_id)
    for item in items:
        item["active"]=item["release_id"]==state.get("active_release_id")
    return items


def deployment_status(site_id: str) -> dict[str, Any]:
    site=hosting_runtime.get_site(site_id)
    state=_read_state(site_id)
    active=state.get("active_release_id")
    return {
        "site_id":site_id,
        "site_state":site["state"],
        "active_release_id":active,
        "previous_release_id":state.get("previous_release_id"),
        "active_release":_release_manifest(site_id,active) if active else None,
    }


def deploy_package(site_id: str, package: bytes, *, request_key: str | None=None) -> dict[str, Any]:
    site=hosting_runtime.get_site(site_id)
    if site["state"]=="suspended":
        raise DeploymentError("Suspended sites cannot receive deployments.",409)
    archive,manifest,digest=_validate_zip(package)
    runtime=str(manifest["runtime"]).lower()
    if runtime != str(site["runtime_kind"]).lower():
        archive.close()
        raise DeploymentError("Deployment runtime does not match the site runtime.",409)

    if request_key:
        with db() as connection:
            rows=connection.execute(
                "SELECT details_json FROM hosting_runtime_events WHERE site_id=? AND event_type='deployment.activated' ORDER BY id DESC LIMIT 100",
                (site_id,),
            ).fetchall()
        for row in rows:
            try:
                details=json.loads(row["details_json"])
            except Exception:
                continue
            if details.get("request_key")==request_key:
                release_id=str(details.get("release_id") or "")
                if release_id:
                    archive.close()
                    return _release_manifest(site_id,release_id)

    release_id="release_"+uuid.uuid4().hex[:24]
    root=releases_root(site_id)
    staging=Path(tempfile.mkdtemp(prefix=".staging-",dir=root))
    final=root/release_id
    try:
        content=staging/"content"
        content.mkdir()
        for info in archive.infolist():
            path=_normalize_member(info.filename)
            target=(content/Path(*path.parts)).resolve()
            if content.resolve() not in target.parents and target != content.resolve():
                raise DeploymentError("Deployment package escaped its staging root.")
            if info.is_dir():
                target.mkdir(parents=True,exist_ok=True)
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info,"r") as source, target.open("wb") as destination:
                shutil.copyfileobj(source,destination,1024*1024)
        archive.close()

        entry=(content/Path(*_normalize_member(str(manifest["entrypoint"])).parts)).resolve()
        if not entry.is_file():
            raise DeploymentError("Deployment entrypoint was not extracted.")

        release_manifest={
            "contract":"vp3.hosting.release.v1",
            "release_id":release_id,
            "site_id":site_id,
            "package_sha256":digest,
            "runtime":runtime,
            "entrypoint":str(manifest["entrypoint"]),
            "app_version":str(manifest.get("version") or ""),
            "created_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        (staging/"release.json").write_text(json.dumps(release_manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        os.replace(staging,final)

        old=_read_state(site_id)
        next_state={
            "contract":"vp3.hosting.deployment-state.v1",
            "site_id":site_id,
            "active_release_id":release_id,
            "previous_release_id":old.get("active_release_id"),
        }
        _write_state(site_id,next_state)
        hosting_runtime.set_state(site_id,"active")
        _record(site_id,"deployment.activated","active",{
            "release_id":release_id,
            "package_sha256":digest,
            "request_key":request_key,
            "runtime":runtime,
        })
        result=dict(release_manifest)
        result["active"]=True
        result["previous_release_id"]=next_state["previous_release_id"]
        return result
    except Exception:
        archive.close()
        shutil.rmtree(staging,ignore_errors=True)
        raise


def rollback(site_id: str) -> dict[str, Any]:
    hosting_runtime.get_site(site_id)
    state=_read_state(site_id)
    previous=state.get("previous_release_id")
    active=state.get("active_release_id")
    if not previous:
        raise DeploymentError("No previous release is available for rollback.",409)
    previous_manifest=_release_manifest(site_id,str(previous))
    _write_state(site_id,{
        "contract":"vp3.hosting.deployment-state.v1",
        "site_id":site_id,
        "active_release_id":previous,
        "previous_release_id":active,
    })
    hosting_runtime.set_state(site_id,"active")
    _record(site_id,"deployment.rolled_back","active",{"release_id":previous,"replaced_release_id":active})
    result=dict(previous_manifest)
    result["active"]=True
    result["previous_release_id"]=active
    return result


def active_public_root(site_id: str) -> Path:
    status=deployment_status(site_id)
    active=status.get("active_release")
    if not active:
        raise DeploymentError("Hosted site has no active deployment.",409)
    root=(releases_root(site_id)/str(active["release_id"])/"content"/"public").resolve()
    releases=releases_root(site_id).resolve()
    if releases not in root.parents:
        raise DeploymentError("Active release escaped its site root.",500)
    if not root.is_dir():
        raise DeploymentError("Active release public root is missing.",500)
    return root


def public_capability() -> dict[str, Any]:
    return {
        "contract":DEPLOYMENT_CONTRACT,
        "zip_upload":True,
        "atomic_release_activation":True,
        "rollback":True,
        "request_idempotency":True,
        "max_package_bytes":MAX_PACKAGE_BYTES,
        "max_uncompressed_bytes":MAX_UNCOMPRESSED_BYTES,
        "max_files":MAX_FILES,
        "caller_filesystem_paths":False,
        "symbolic_links":False,
        "public_serving":False,
    }

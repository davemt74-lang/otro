from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..database import db
from . import homeserver_app_data_lifecycle, homeserver_app_packages, homeserver_app_runtime, homeserver_apps

CONTRACT="vp3.app.release-management.v1"
MAX_RELEASES=20


class AppReleaseError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _root(app_key:str)->Path:
    return homeserver_app_packages.releases_root(app_key)


def _release(app_key:str,release_id:str)->dict[str,Any]:
    rid=str(release_id or "").strip()
    if not rid.startswith("apprel_") or len(rid)>80:
        raise AppReleaseError("App release id is invalid.")
    path=_root(app_key)/rid/"release.json"
    if not path.is_file() or path.is_symlink():
        raise AppReleaseError("App release not found.",404)
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppReleaseError("App release metadata is invalid.",500) from exc
    if not isinstance(payload,dict) or payload.get("release_id")!=rid or payload.get("app_key")!=app_key:
        raise AppReleaseError("App release metadata is invalid.",500)
    return payload


def list_releases(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    state=homeserver_app_packages._read_state(app_key)
    rows=[]
    root=_root(app_key)
    for directory in root.iterdir():
        if not directory.is_dir() or not directory.name.startswith("apprel_"):
            continue
        try:
            release=_release(app_key,directory.name)
        except AppReleaseError:
            continue
        release=dict(release)
        release["active"]=release["release_id"]==state.get("active_release_id")
        release["previous"]=release["release_id"]==state.get("previous_release_id")
        try:
            release["mtime"]=directory.stat().st_mtime
        except OSError:
            release["mtime"]=0
        rows.append(release)
    rows.sort(key=lambda item:(float(item.get("mtime") or 0),str(item.get("release_id") or "")),reverse=True)
    for row in rows:
        row.pop("mtime",None)
    return {
        "contract":CONTRACT,
        "app_key":app_key,
        "lifecycle_state":app["lifecycle_state"],
        "active_release_id":state.get("active_release_id"),
        "previous_release_id":state.get("previous_release_id"),
        "releases":rows,
        "count":len(rows),
    }


def promote(app_key:str,release_id:str,*,reason:str="owner_promotion",system_managed:bool=False)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]=="system" and not system_managed:
        raise AppReleaseError("VP3 system app releases are managed by VP3.",409)
    release=_release(app_key,release_id)
    content=(_root(app_key)/release["release_id"]/"content").resolve()
    base=_root(app_key).resolve()
    if base not in content.parents or not content.is_dir():
        raise AppReleaseError("App release content is unavailable.",500)
    try:
        homeserver_app_runtime.validate_release_contracts(app_key,content)
    except homeserver_app_runtime.AppRuntimeError as exc:
        raise AppReleaseError(str(exc),exc.status_code) from exc
    state=homeserver_app_packages._read_state(app_key)
    active=state.get("active_release_id")
    if active==release["release_id"] and app["lifecycle_state"]=="running":
        return {"changed":False,"reason":"already_active","release":release,"status":list_releases(app_key)}
    actor_type="system" if system_managed else "owner"
    actor_key="vp3_prebuilt" if system_managed else "local_owner"
    current=str(app["lifecycle_state"])
    if current not in {"running","degraded","stopped","failed","installed","updating","recovering"}:
        raise AppReleaseError("App cannot promote a release from its current lifecycle state.",409)
    next_state={
        "contract":homeserver_app_packages.RUNTIME_CONTRACT,
        "app_key":app_key,
        "active_release_id":release["release_id"],
        "previous_release_id":active,
    }
    homeserver_app_packages._write_state(app_key,next_state)
    metadata=dict(app.get("metadata") or {})
    metadata.update({
        "active_release_id":release["release_id"],
        "previous_release_id":active,
        "package_sha256":release.get("package_sha256",""),
        "runtime":release.get("runtime",""),
        "entrypoint":release.get("entrypoint",""),
        "sdk_version":release.get("sdk_version",""),
    })
    try:
        homeserver_app_runtime.sync_release(app_key,content)
        with db() as connection:
            connection.execute(
                """UPDATE homeserver_apps SET installed_version=?,desired_version=?,lifecycle_state='running',
                   metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_key=?""",
                (
                    str(release.get("version") or ""),
                    str(release.get("version") or ""),
                    json.dumps(metadata,separators=(",",":"),sort_keys=True),
                    app_key,
                ),
            )
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,from_state,to_state,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.release.promoted', ?, 'running', ?, ?, ?)""",
                (
                    app["app_id"],current,actor_type,actor_key,
                    json.dumps({"release_id":release["release_id"],"previous_release_id":active,"reason":reason},separators=(",",":"),sort_keys=True),
                ),
            )
    except Exception:
        homeserver_app_packages._write_state(app_key,state)
        if active:
            previous_content=(_root(app_key)/str(active)/"content").resolve()
            if previous_content.is_dir():
                try:
                    homeserver_app_runtime.sync_release(app_key,previous_content)
                except Exception:
                    pass
        raise
    return {"changed":True,"release":release,"status":list_releases(app_key)}


def rollback(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]=="system":
        raise AppReleaseError("VP3 system app rollback is managed by VP3.",409)
    state=homeserver_app_packages._read_state(app_key)
    active=str(state.get("active_release_id") or "")
    previous=str(state.get("previous_release_id") or "")
    if not previous:
        raise AppReleaseError("No previous app release is available.",409)
    active_release=_release(app_key,active) if active else {}
    migration=dict(active_release.get("data_migration") or {})
    if migration.get("migration_required"):
        if not migration.get("migration_reversible"):
            raise AppReleaseError(
                "Rollback is blocked because the active release contains an irreversible app data migration.",
                409,
            )
        if not str(migration.get("snapshot_id") or ""):
            raise AppReleaseError(
                "Rollback is blocked because the required app data recovery snapshot is unavailable.",
                409,
            )
    result=promote(app_key,previous,reason="owner_rollback")
    try:
        data_restore=homeserver_app_data_lifecycle.rollback_data_for_active_release(app_key,active_release)
    except Exception as exc:
        if active:
            try:
                promote(app_key,active,reason="rollback_data_restore_failed")
            except Exception:
                pass
        raise AppReleaseError(f"App data rollback failed: {exc}",500) from exc
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.release.rolled_back','owner','local_owner',?)""",
            (app["app_id"],json.dumps({"release_id":previous,"data_restore":data_restore},separators=(",",":"),sort_keys=True)),
        )
    result["data_restore"]=data_restore
    return result


def recover(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]=="system":
        raise AppReleaseError("VP3 system app recovery is managed by VP3.",409)
    if app["lifecycle_state"] not in {"failed","degraded"}:
        raise AppReleaseError("Recovery is only available for failed or degraded apps.",409)
    state=homeserver_app_packages._read_state(app_key)
    candidate=str(state.get("previous_release_id") or state.get("active_release_id") or "")
    if not candidate:
        raise AppReleaseError("No recovery release is available.",409)
    result=promote(app_key,candidate,reason="owner_recovery")
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.release.recovered','owner','local_owner',?)""",
            (app["app_id"],json.dumps({"release_id":candidate},separators=(",",":"),sort_keys=True)),
        )
    return result


def prune(app_key:str,keep:int=MAX_RELEASES)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]=="system":
        raise AppReleaseError("VP3 system app retention is managed by VP3.",409)
    keep=max(2,min(MAX_RELEASES,int(keep)))
    status=list_releases(app_key)
    protected={status.get("active_release_id"),status.get("previous_release_id")}
    removed=[]
    for release in status["releases"][keep:]:
        rid=release["release_id"]
        if rid in protected:
            continue
        path=_root(app_key)/rid
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(rid)
    if removed:
        with db() as connection:
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.releases.pruned','owner','local_owner',?)""",
                (app["app_id"],json.dumps({"removed":removed,"keep":keep},separators=(",",":"),sort_keys=True)),
            )
    return {"removed":removed,"status":list_releases(app_key)}


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "release_history":True,
        "manual_promotion":True,
        "rollback":True,
        "failed_release_recovery":True,
        "atomic_activation":True,
        "protected_system_app_release_control":True,
        "max_retained_releases":MAX_RELEASES,
    }

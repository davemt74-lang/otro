from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database import db
from . import hosting_deployment, hosting_runtime

CONTRACT="vp3.hosting.recovery.v1"
MAX_RECOVERY_POINTS=12


class RecoveryError(hosting_runtime.HostingError):
    pass


def _now()->str:
    return datetime.now(timezone.utc).isoformat()


def _root(site_id:str)->Path:
    root=hosting_runtime.site_root(site_id)/"backups"/"recovery"
    root.mkdir(parents=True,exist_ok=True)
    hosting_runtime._ensure_no_symlink(root)
    return root


def _sha(path:Path)->str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_sqlite(site_id:str,destination:Path)->dict[str,Any]:
    source=hosting_runtime.connect_site_db(site_id)
    target=sqlite3.connect(destination)
    try:
        source.execute("PRAGMA wal_checkpoint(FULL)")
        source.backup(target)
        target.commit()
        integrity=str(target.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity!="ok":
            raise RecoveryError("Recovery-point SQLite integrity check failed.",500)
    finally:
        target.close()
        source.close()
    return {"bytes":destination.stat().st_size,"sha256":_sha(destination),"integrity":"ok"}


def _snapshot_storage(site_id:str,destination:Path)->dict[str,Any]:
    storage=(hosting_runtime.site_root(site_id)/"storage").resolve()
    hosting_runtime._ensure_no_symlink(storage)
    count=0
    total=0
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
        if storage.exists():
            for base,dirs,files in os.walk(storage):
                base_path=Path(base)
                dirs[:]=[d for d in dirs if not (base_path/d).is_symlink()]
                for name in files:
                    path=base_path/name
                    if path.is_symlink():
                        continue
                    resolved=path.resolve()
                    if resolved!=storage and storage not in resolved.parents:
                        raise RecoveryError("Storage path escaped the hosted site root.",409)
                    rel=resolved.relative_to(storage).as_posix()
                    archive.write(resolved,rel)
                    count+=1
                    total+=resolved.stat().st_size
    return {"files":count,"bytes":total,"archive_bytes":destination.stat().st_size,"sha256":_sha(destination)}


def _deployment_snapshot(site_id:str,destination:Path)->dict[str,Any]:
    state=hosting_deployment.deployment_status(site_id)
    payload={
        "contract":"vp3.hosting.deployment-snapshot.v1",
        "site_id":site_id,
        "active_release_id":state.get("active_release_id"),
        "previous_release_id":state.get("previous_release_id"),
    }
    destination.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return {"sha256":_sha(destination)}


def _restore_sqlite_snapshot(site_id:str,snapshot:Path)->None:
    source=sqlite3.connect(snapshot)
    target=hosting_runtime.connect_site_db(site_id)
    try:
        integrity=str(source.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity!="ok":
            raise RecoveryError("Restore source SQLite integrity check failed.",409)
        source.backup(target)
        target.commit()
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        restored=str(target.execute("PRAGMA integrity_check").fetchone()[0])
        if restored!="ok":
            raise RecoveryError("Restored SQLite database failed integrity verification.",409)
    finally:
        target.close()
        source.close()


def _manifest_path(site_id:str,recovery_id:str)->Path:
    return _root(site_id)/recovery_id/"recovery.json"


def _load(site_id:str,recovery_id:str)->dict[str,Any]:
    rid=str(recovery_id or "").strip()
    if not rid.startswith("recovery_") or len(rid)>80:
        raise RecoveryError("Invalid recovery point identifier.")
    path=_manifest_path(site_id,rid)
    if not path.is_file():
        raise RecoveryError("Recovery point not found.",404)
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise RecoveryError("Recovery point metadata is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("site_id")!=site_id or payload.get("recovery_id")!=rid:
        raise RecoveryError("Recovery point metadata is invalid.",500)
    return payload


def verify(site_id:str,recovery_id:str)->dict[str,Any]:
    manifest=_load(site_id,recovery_id)
    directory=_manifest_path(site_id,recovery_id).parent
    for key,filename in (("sqlite","site.sqlite"),("storage","storage.zip"),("deployment","deployment.json")):
        path=directory/filename
        expected=str((manifest.get(key) or {}).get("sha256") or "")
        if not path.is_file() or not expected or _sha(path)!=expected:
            raise RecoveryError(f"Recovery point {key} checksum verification failed.",409)
    check=sqlite3.connect(directory/"site.sqlite")
    try:
        integrity=str(check.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        check.close()
    if integrity!="ok":
        raise RecoveryError("Recovery point SQLite integrity check failed.",409)
    return {
        "recovery_id":recovery_id,
        "site_id":site_id,
        "verified":True,
        "created_at":manifest.get("created_at"),
        "reason":manifest.get("reason"),
        "sqlite_bytes":(manifest.get("sqlite") or {}).get("bytes"),
        "storage_bytes":(manifest.get("storage") or {}).get("bytes"),
        "storage_files":(manifest.get("storage") or {}).get("files"),
    }


def list_recovery_points(site_id:str)->list[dict[str,Any]]:
    hosting_runtime.get_site(site_id)
    items=[]
    for child in _root(site_id).iterdir():
        if not child.is_dir() or not child.name.startswith("recovery_"):
            continue
        try:
            manifest=_load(site_id,child.name)
            items.append({
                "recovery_id":child.name,
                "site_id":site_id,
                "created_at":manifest.get("created_at"),
                "reason":manifest.get("reason"),
                "sqlite_bytes":(manifest.get("sqlite") or {}).get("bytes"),
                "storage_bytes":(manifest.get("storage") or {}).get("bytes"),
                "storage_files":(manifest.get("storage") or {}).get("files"),
            })
        except RecoveryError:
            continue
    items.sort(key=lambda item:(str(item.get("created_at") or ""),str(item["recovery_id"])),reverse=True)
    return items


def prune(site_id:str,keep:int=MAX_RECOVERY_POINTS)->int:
    keep=max(1,min(int(keep),100))
    items=list_recovery_points(site_id)
    removed=0
    for item in items[keep:]:
        shutil.rmtree(_manifest_path(site_id,item["recovery_id"]).parent,ignore_errors=True)
        removed+=1
    return removed


def create_recovery_point(site_id:str,*,reason:str="manual")->dict[str,Any]:
    site=hosting_runtime.get_site(site_id)
    rid="recovery_"+uuid.uuid4().hex[:24]
    final=_root(site_id)/rid
    staging=Path(tempfile.mkdtemp(prefix=".staging-",dir=_root(site_id)))
    try:
        sqlite_info=_snapshot_sqlite(site_id,staging/"site.sqlite")
        storage_info=_snapshot_storage(site_id,staging/"storage.zip")
        deployment_info=_deployment_snapshot(site_id,staging/"deployment.json")
        manifest={
            "contract":CONTRACT,
            "recovery_id":rid,
            "site_id":site_id,
            "created_at":_now(),
            "reason":str(reason or "manual")[:120],
            "site_state":site["state"],
            "runtime_kind":site["runtime_kind"],
            "requested_hostname":site.get("requested_hostname"),
            "sqlite":sqlite_info,
            "storage":storage_info,
            "deployment":deployment_info,
        }
        (staging/"recovery.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        os.replace(staging,final)
        verify(site_id,rid)
        with db() as connection:
            connection.execute(
                "INSERT INTO hosting_runtime_events(site_id,event_type,state,details_json) VALUES (?,?,?,?)",
                (site_id,"recovery.created",site["state"],json.dumps({"recovery_id":rid,"reason":manifest["reason"]},separators=(",",":"))),
            )
        prune(site_id)
        result=verify(site_id,rid)
        result["pruned_to"]=MAX_RECOVERY_POINTS
        return result
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise


def _extract_storage(archive_path:Path,destination:Path)->None:
    destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive_path,"r") as archive:
        for info in archive.infolist():
            raw=str(info.filename or "").replace("\\","/")
            parts=Path(raw).parts
            if not raw or raw.startswith("/") or any(part in {"",".."} for part in parts):
                raise RecoveryError("Recovery storage archive contains an unsafe path.",409)
            target=(destination/Path(*parts)).resolve()
            root=destination.resolve()
            if target!=root and root not in target.parents:
                raise RecoveryError("Recovery storage archive escaped its staging root.",409)
            mode=(info.external_attr>>16)&0o170000
            if mode==0o120000:
                raise RecoveryError("Recovery storage archive contains a symbolic link.",409)
            if info.is_dir():
                target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(info,"r") as src,target.open("wb") as dst:
                    shutil.copyfileobj(src,dst,1024*1024)


def restore(site_id:str,recovery_id:str)->dict[str,Any]:
    site=hosting_runtime.get_site(site_id)
    verified=verify(site_id,recovery_id)
    source_dir=_manifest_path(site_id,recovery_id).parent

    pre=create_recovery_point(site_id,reason=f"pre-restore:{recovery_id}")
    root=hosting_runtime.site_root(site_id)
    staging=Path(tempfile.mkdtemp(prefix=".restore-",dir=root))
    old_storage=root/"storage"
    rollback_storage=root/".storage-before-restore"
    db_path=hosting_runtime.site_db_path(site_id)
    rollback_db=root/"database"/".site-before-restore.sqlite"

    try:
        shutil.copy2(source_dir/"site.sqlite",staging/"site.sqlite")
        check=sqlite3.connect(staging/"site.sqlite")
        try:
            if str(check.execute("PRAGMA integrity_check").fetchone()[0])!="ok":
                raise RecoveryError("Staged restore database failed integrity verification.",409)
        finally:
            check.close()

        staged_storage=staging/"storage"
        _extract_storage(source_dir/"storage.zip",staged_storage)

        hosting_runtime.set_state(site_id,"suspended")
        if rollback_storage.exists():
            shutil.rmtree(rollback_storage,ignore_errors=True)
        os.replace(old_storage,rollback_storage)
        os.replace(staged_storage,old_storage)

        if rollback_db.exists():
            rollback_db.unlink()
        _snapshot_sqlite(site_id,rollback_db)
        _restore_sqlite_snapshot(site_id,staging/"site.sqlite")

        health=hosting_runtime.database_health(site_id)
        if not health.get("healthy"):
            raise RecoveryError("Restored SQLite database is unhealthy.",409)

        deployment=json.loads((source_dir/"deployment.json").read_text(encoding="utf-8"))
        active_release_id=deployment.get("active_release_id")
        previous_release_id=deployment.get("previous_release_id")
        if active_release_id:
            hosting_deployment._release_manifest(site_id,str(active_release_id))
        if previous_release_id:
            hosting_deployment._release_manifest(site_id,str(previous_release_id))
        state_payload={
            "contract":"vp3.hosting.deployment-state.v1",
            "site_id":site_id,
            "active_release_id":active_release_id,
            "previous_release_id":previous_release_id,
        }
        hosting_deployment._write_state(site_id,state_payload)

        desired_state="active" if state_payload.get("active_release_id") else "configured"
        hosting_runtime.set_state(site_id,desired_state)
        shutil.rmtree(rollback_storage,ignore_errors=True)
        rollback_db.unlink(missing_ok=True)
        with db() as connection:
            connection.execute(
                "INSERT INTO hosting_runtime_events(site_id,event_type,state,details_json) VALUES (?,?,?,?)",
                (site_id,"recovery.restored",desired_state,json.dumps({"recovery_id":recovery_id,"pre_restore_recovery_id":pre["recovery_id"]},separators=(",",":"))),
            )
        return {
            "site_id":site_id,
            "recovery_id":recovery_id,
            "restored":True,
            "pre_restore_recovery_id":pre["recovery_id"],
            "state":desired_state,
            "verified":verified["verified"],
        }
    except Exception:
        try:
            if rollback_storage.exists():
                shutil.rmtree(old_storage,ignore_errors=True)
                os.replace(rollback_storage,old_storage)
            if rollback_db.exists():
                _restore_sqlite_snapshot(site_id,rollback_db)
                rollback_db.unlink(missing_ok=True)
            hosting_runtime.set_state(site_id,site["state"])
        except Exception:
            try:
                hosting_runtime.set_state(site_id,"failed")
            except Exception:
                pass
        raise
    finally:
        shutil.rmtree(staging,ignore_errors=True)


def recovery_health(site_id:str)->dict[str,Any]:
    hosting_runtime.get_site(site_id)
    items=list_recovery_points(site_id)
    latest=items[0] if items else None
    latest_verified=False
    if latest:
        try:
            latest_verified=bool(verify(site_id,latest["recovery_id"]).get("verified"))
        except RecoveryError:
            latest_verified=False
    return {
        "contract":CONTRACT,
        "site_id":site_id,
        "recovery_points":len(items),
        "latest_recovery_id":latest.get("recovery_id") if latest else None,
        "latest_created_at":latest.get("created_at") if latest else None,
        "latest_verified":latest_verified,
        "retention_limit":MAX_RECOVERY_POINTS,
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "site_recovery_points":True,
        "sqlite_snapshot":True,
        "storage_snapshot":True,
        "deployment_state_snapshot":True,
        "checksum_verification":True,
        "sqlite_integrity_verification":True,
        "pre_restore_recovery_point":True,
        "atomic_staging":True,
        "retention_limit":MAX_RECOVERY_POINTS,
        "cloud_visible_health":True,
        "remote_restore":False,
        "filesystem_paths_remote":False,
    }

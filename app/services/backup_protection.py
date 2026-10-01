from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db

CONTRACT="vp3.homeserver.backup-protection.v1"
APP_DATA_PREFIX="app-data/"
_SQLITE_BUSY_MS=30000


class BackupProtectionError(RuntimeError):
    def __init__(self,message:str,status_code:int=422):
        super().__init__(message)
        self.status_code=status_code


def policy()->dict[str,Any]:
    with db() as connection:
        row=connection.execute(
            """SELECT include_app_data,retain_manual,retain_automatic,retain_pre_restore,updated_at
               FROM backup_policy WHERE singleton_id=1"""
        ).fetchone()
    if row is None:
        return {
            "include_app_data":True,
            "retain_manual":10,
            "retain_automatic":7,
            "retain_pre_restore":3,
            "updated_at":None,
        }
    return {
        "include_app_data":bool(row["include_app_data"]),
        "retain_manual":int(row["retain_manual"]),
        "retain_automatic":int(row["retain_automatic"]),
        "retain_pre_restore":int(row["retain_pre_restore"]),
        "updated_at":row["updated_at"],
    }


def update_policy(values:dict[str,Any])->dict[str,Any]:
    allowed={"include_app_data","retain_manual","retain_automatic","retain_pre_restore"}
    unknown=set(values)-allowed
    if unknown:
        raise BackupProtectionError(f"Unsupported backup policy field: {sorted(unknown)[0]}")
    current=policy()
    include=bool(values.get("include_app_data",current["include_app_data"]))
    manual=int(values.get("retain_manual",current["retain_manual"]))
    automatic=int(values.get("retain_automatic",current["retain_automatic"]))
    pre_restore=int(values.get("retain_pre_restore",current["retain_pre_restore"]))
    if not 1<=manual<=100 or not 1<=automatic<=100 or not 1<=pre_restore<=20:
        raise BackupProtectionError("Backup retention value is outside the supported range.")
    with db() as connection:
        connection.execute(
            """INSERT INTO backup_policy(
                 singleton_id,include_app_data,retain_manual,retain_automatic,retain_pre_restore,updated_at
               ) VALUES (1,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 include_app_data=excluded.include_app_data,
                 retain_manual=excluded.retain_manual,
                 retain_automatic=excluded.retain_automatic,
                 retain_pre_restore=excluded.retain_pre_restore,
                 updated_at=CURRENT_TIMESTAMP""",
            (1 if include else 0,manual,automatic,pre_restore),
        )
    return policy()


def _snapshot_sqlite(source:Path,target:Path)->None:
    target.parent.mkdir(parents=True,exist_ok=True)
    source_connection=sqlite3.connect(source,timeout=30)
    destination=sqlite3.connect(target,timeout=30)
    try:
        source_connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_MS}")
        destination.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_MS}")
        source_connection.backup(destination)
        destination.commit()
        quick=[str(row[0]) for row in destination.execute("PRAGMA quick_check").fetchall()]
        if quick!=["ok"]:
            raise BackupProtectionError("App SQLite snapshot failed integrity validation.")
    except sqlite3.DatabaseError as exc:
        raise BackupProtectionError("App SQLite database could not be snapshotted safely.") from exc
    finally:
        destination.close()
        source_connection.close()


def _copy_regular(source:Path,target:Path)->None:
    if source.is_symlink():
        raise BackupProtectionError("App data contains an unsupported symbolic link.")
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)


def snapshot_app_data(target_root:Path)->dict[str,Any]:
    source_root=settings.data_dir/"app-data"
    output_root=target_root/"app-data"
    apps:list[dict[str,Any]]=[]
    paths:list[str]=[]
    total_bytes=0
    total_files=0
    if not source_root.exists():
        return {"archive_paths":[],"apps":[],"bytes":0,"files":0}

    with db() as connection:
        rows=connection.execute(
            """SELECT app_id,app_key,name,installed_version,metadata_json
               FROM homeserver_apps ORDER BY app_key"""
        ).fetchall()
    by_id={str(row["app_id"]):row for row in rows}

    for source_app_root in sorted(source_root.iterdir(),key=lambda p:p.name):
        if source_app_root.is_symlink():
            raise BackupProtectionError("App data root contains an unsupported symbolic link.")
        if not source_app_root.is_dir():
            raise BackupProtectionError("App data root contains an unexpected file.")
        app_id=source_app_root.name
        row=by_id.get(app_id)
        if row is None:
            raise BackupProtectionError("App data exists for an app that is not registered.")
        app_bytes=0
        app_files=0
        destination_root=output_root/app_id
        for source in sorted(source_app_root.rglob("*")):
            if source.is_symlink():
                raise BackupProtectionError("App data contains an unsupported symbolic link.")
            if not source.is_file():
                continue
            relative=source.relative_to(source_app_root)
            target=destination_root/relative
            if relative.parts and relative.parts[0]=="sqlite" and source.suffix.lower()==".db":
                _snapshot_sqlite(source,target)
            else:
                _copy_regular(source,target)
            size=target.stat().st_size
            app_bytes+=size
            app_files+=1
            total_bytes+=size
            total_files+=1
            if total_bytes>settings.max_backup_uncompressed_bytes:
                raise BackupProtectionError("App data exceeds the configured backup safety limit.",413)
            paths.append(f"app-data/{app_id}/{relative.as_posix()}")
        try:
            metadata=json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata={}
        apps.append({
            "app_id":app_id,
            "app_key":str(row["app_key"]),
            "name":str(row["name"]),
            "installed_version":str(row["installed_version"] or ""),
            "data_schema_version":str(metadata.get("data_schema_version") or "1"),
            "files":app_files,
            "bytes":app_bytes,
        })
    return {
        "archive_paths":paths,
        "apps":apps,
        "bytes":total_bytes,
        "files":total_files,
    }


def validate_app_data(target_root:Path,database_path:Path,manifest:dict[str,Any])->dict[str,Any]:
    version=int(manifest.get("format_version") or 1)
    coverage=manifest.get("coverage") if isinstance(manifest.get("coverage"),dict) else {}
    if version<2:
        return {
            "included":False,
            "legacy_backup":True,
            "apps":[],
            "files":0,
            "bytes":0,
            "warning":"Legacy v1 backup does not contain app data; existing app-data files will be preserved.",
        }
    if not bool(coverage.get("app_data")):
        return {
            "included":False,
            "legacy_backup":False,
            "apps":[],
            "files":0,
            "bytes":0,
            "warning":"This v2 backup was created without app-data coverage; existing app-data files will be preserved.",
        }

    app_root=target_root/"app-data"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory=sqlite3.Row
        table=connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='homeserver_apps'"
        ).fetchone()
        rows=connection.execute(
            "SELECT app_id,app_key,installed_version FROM homeserver_apps"
        ).fetchall() if table else []
    known={str(row["app_id"]):dict(row) for row in rows}

    files=0
    total=0
    seen_apps:set[str]=set()
    if app_root.exists():
        for app_dir in sorted(app_root.iterdir(),key=lambda p:p.name):
            if app_dir.is_symlink() or not app_dir.is_dir():
                raise BackupProtectionError("Backup app-data tree is invalid.")
            app_id=app_dir.name
            if app_id not in known:
                raise BackupProtectionError("Backup contains app data for an unregistered app.")
            seen_apps.add(app_id)
            for path in sorted(app_dir.rglob("*")):
                if path.is_symlink():
                    raise BackupProtectionError("Backup app data contains a symbolic link.")
                if not path.is_file():
                    continue
                files+=1
                total+=path.stat().st_size
                rel=path.relative_to(app_dir)
                if rel.parts and rel.parts[0]=="sqlite" and path.suffix.lower()==".db":
                    try:
                        connection=sqlite3.connect(path)
                        quick=[str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
                    except sqlite3.DatabaseError as exc:
                        raise BackupProtectionError("Backup contains an invalid app SQLite database.") from exc
                    finally:
                        try: connection.close()
                        except Exception: pass
                    if quick!=["ok"]:
                        raise BackupProtectionError("Backup app SQLite database failed integrity validation.")
    declared=manifest.get("apps")
    if declared is not None:
        if not isinstance(declared,list):
            raise BackupProtectionError("Backup app inventory is invalid.")
        declared_ids={str(item.get("app_id") or "") for item in declared if isinstance(item,dict)}
        if declared_ids!=seen_apps and any(int(item.get("files") or 0)>0 for item in declared if isinstance(item,dict)):
            raise BackupProtectionError("Backup app inventory does not match its app-data tree.")
    return {
        "included":True,
        "legacy_backup":False,
        "apps":[
            {"app_id":app_id,"app_key":known[app_id]["app_key"],"installed_version":known[app_id]["installed_version"]}
            for app_id in sorted(seen_apps)
        ],
        "files":files,
        "bytes":total,
        "warning":None,
    }


def prepare_app_data_restore(pending:Path,token:str)->dict[str,Any]:
    staged=pending/"app-data"
    target=settings.data_dir/"app-data"
    try:
        manifest=json.loads((pending/"manifest.json").read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise BackupProtectionError("Pending restore manifest is unavailable for app-data preparation.") from exc
    coverage=manifest.get("coverage") if isinstance(manifest.get("coverage"),dict) else {}
    included=int(manifest.get("format_version") or 1)>=2 and bool(coverage.get("app_data"))
    if not included:
        return {"included":False,"new":None,"old":None,"target":target,"had_target":target.exists()}
    if not staged.is_dir():
        staged.mkdir(parents=True,exist_ok=True)
    new=settings.data_dir/f".app-data-restore-new-{token}"
    old=settings.data_dir/f".app-data-restore-old-{token}"
    shutil.copytree(staged,new,symlinks=False)
    return {"included":True,"new":new,"old":old,"target":target,"had_target":target.exists()}


def swap_app_data(state:dict[str,Any])->None:
    if not state.get("included"):
        return
    target:Path=state["target"]
    new:Path=state["new"]
    old:Path=state["old"]
    if target.exists():
        os.replace(target,old)
    os.replace(new,target)
    state["swapped"]=True


def rollback_app_data(state:dict[str,Any])->None:
    if not state.get("included"):
        return
    target:Path=state["target"]
    old:Path=state["old"]
    if state.get("swapped") and target.exists():
        shutil.rmtree(target,ignore_errors=True)
    if old.exists():
        os.replace(old,target)
    elif not state.get("had_target"):
        shutil.rmtree(target,ignore_errors=True)


def finalize_app_data(state:dict[str,Any])->None:
    if not state.get("included"):
        return
    new:Path=state["new"]
    old:Path=state["old"]
    shutil.rmtree(new,ignore_errors=True)
    shutil.rmtree(old,ignore_errors=True)


def invalidate_restored_sessions()->int:
    with db() as connection:
        exists=connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='homeserver_member_sessions'"
        ).fetchone()
        if not exists:
            return 0
        count=int(connection.execute("SELECT COUNT(*) FROM homeserver_member_sessions").fetchone()[0])
        connection.execute("DELETE FROM homeserver_member_sessions")
    return count


def prune_backups(items:list[dict[str,Any]],delete_callback,*,protect_name:str="")->dict[str,Any]:
    rules=policy()
    groups={
        "manual":rules["retain_manual"],
        "automatic":rules["retain_automatic"],
        "pre-restore":rules["retain_pre_restore"],
    }
    deleted:list[str]=[]
    for reason,retain in groups.items():
        matching=[item for item in items if not item.get("invalid") and str(item.get("reason") or "")==reason]
        matching.sort(key=lambda row:str(row.get("created_at") or ""),reverse=True)
        for item in matching[int(retain):]:
            name=str(item.get("name") or "")
            if not name or name==protect_name:
                continue
            delete_callback(name)
            deleted.append(name)
    return {"deleted":deleted,"deleted_count":len(deleted)}


def health(backups:list[dict[str,Any]],last_restore:dict[str,Any]|None,pending_restore:dict[str,Any]|None)->dict[str,Any]:
    valid=[item for item in backups if not item.get("invalid")]
    invalid=[item for item in backups if item.get("invalid")]
    latest=valid[0] if valid else None
    return {
        "contract":CONTRACT,
        "backup_format_current":2,
        "legacy_v1_restore_supported":True,
        "backup_count":len(valid),
        "invalid_backup_count":len(invalid),
        "latest_backup":latest,
        "pending_restore":pending_restore,
        "last_restore":last_restore,
        "policy":policy(),
        "coverage":{
            "database":True,
            "knowledge_files":True,
            "app_data":True,
            "member_data":"database",
            "security_secrets":False,
            "runtime_state":False,
            "backup_archives":False,
            "restore_staging":False,
            "app_recovery_snapshots":False,
        },
        "session_invalidation_after_restore":True,
        "atomic_app_data_restore":True,
        "sqlite_consistent_app_snapshots":True,
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "backup_format_v2":True,
        "legacy_v1_restore":True,
        "app_data_included":True,
        "sqlite_consistent_app_snapshots":True,
        "atomic_app_data_restore":True,
        "restore_preflight":True,
        "member_sessions_invalidated_after_restore":True,
        "retention_policy":True,
        "security_secrets_excluded":True,
        "runtime_state_excluded":True,
    }

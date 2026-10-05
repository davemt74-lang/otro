from __future__ import annotations

from .homeserver_app_locks import serialized

import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import settings
from ..database import db
from . import homeserver_apps

CONTRACT="vp3.app.resource-isolation.v1"
DEFAULT_STORAGE_LIMIT=512*1024*1024
DEFAULT_SQLITE_LIMIT=256*1024*1024
MIN_STORAGE_LIMIT=16*1024*1024
MAX_STORAGE_LIMIT=64*1024*1024*1024
MIN_SQLITE_LIMIT=8*1024*1024
MAX_SQLITE_LIMIT=16*1024*1024*1024
_SAFE_DB_NAME=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\.db$")


class AppResourceError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe_rel(value:str)->PurePosixPath:
    rel=PurePosixPath(str(value or "").replace("\\","/"))
    if not rel.parts or rel.is_absolute() or any(part in {"",".",".."} for part in rel.parts):
        raise AppResourceError("App storage path is invalid.")
    if ":" in rel.parts[0]:
        raise AppResourceError("App storage path is invalid.")
    return rel


def _app_root(app_id:str)->Path:
    root=settings.data_dir/"app-data"/app_id
    root.mkdir(parents=True,exist_ok=True)
    return root


def files_root(app_key:str)->Path:
    app=homeserver_apps.get(app_key)
    root=_app_root(app["app_id"])/"files"
    root.mkdir(parents=True,exist_ok=True)
    return root


def sqlite_root(app_key:str)->Path:
    app=homeserver_apps.get(app_key)
    root=_app_root(app["app_id"])/"sqlite"
    root.mkdir(parents=True,exist_ok=True)
    return root


def _under(root:Path,relative:str)->Path:
    rel=_safe_rel(relative)
    target=(root/Path(*rel.parts)).resolve()
    resolved=root.resolve()
    if target!=resolved and resolved not in target.parents:
        raise AppResourceError("App storage path escaped its isolated root.")
    return target


def _walk_usage(root:Path)->int:
    if not root.exists():
        return 0
    total=0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise AppResourceError("App data root contains an unsupported symbolic link.",500)
        if path.is_file():
            total+=path.stat().st_size
    return total


def _metadata(app:dict[str,Any])->dict[str,Any]:
    return dict(app.get("metadata") or {})


def _limits_from_app(app:dict[str,Any])->dict[str,int]:
    resources=dict(_metadata(app).get("resources") or {})
    return {
        "storage_limit_bytes":int(resources.get("storage_limit_bytes") or DEFAULT_STORAGE_LIMIT),
        "sqlite_limit_bytes":int(resources.get("sqlite_limit_bytes") or DEFAULT_SQLITE_LIMIT),
    }


def limits(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    values=_limits_from_app(app)
    return {"contract":CONTRACT,"app_key":app["app_key"],**values}


@serialized
def update_limits(app_key:str,*,storage_limit_bytes:int|None=None,sqlite_limit_bytes:int|None=None)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    current=_limits_from_app(app)
    storage=current["storage_limit_bytes"] if storage_limit_bytes is None else int(storage_limit_bytes)
    sqlite=current["sqlite_limit_bytes"] if sqlite_limit_bytes is None else int(sqlite_limit_bytes)
    if storage<MIN_STORAGE_LIMIT or storage>MAX_STORAGE_LIMIT:
        raise AppResourceError("Storage limit is outside the supported range.")
    if sqlite<MIN_SQLITE_LIMIT or sqlite>MAX_SQLITE_LIMIT:
        raise AppResourceError("SQLite limit is outside the supported range.")
    usage=resource_status(app_key)
    if storage<usage["storage_used_bytes"]:
        raise AppResourceError("Storage limit cannot be lower than current usage.",409)
    if sqlite<usage["sqlite_used_bytes"]:
        raise AppResourceError("SQLite limit cannot be lower than current usage.",409)
    metadata=_metadata(app)
    resources=dict(metadata.get("resources") or {})
    resources.update({"storage_limit_bytes":storage,"sqlite_limit_bytes":sqlite})
    metadata["resources"]=resources
    with db() as connection:
        connection.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?",
            (json.dumps(metadata,separators=(",",":"),sort_keys=True),app["app_id"]),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.resources.updated','owner','local_owner',?)""",
            (app["app_id"],json.dumps(resources,separators=(",",":"),sort_keys=True)),
        )
    return resource_status(app_key)


def resource_status(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    limits_value=_limits_from_app(app)
    storage_used=_walk_usage(files_root(app_key))
    sqlite_used=_walk_usage(sqlite_root(app_key))
    return {
        "contract":CONTRACT,
        "app_key":app["app_key"],
        **limits_value,
        "storage_used_bytes":storage_used,
        "sqlite_used_bytes":sqlite_used,
        "storage_remaining_bytes":max(0,limits_value["storage_limit_bytes"]-storage_used),
        "sqlite_remaining_bytes":max(0,limits_value["sqlite_limit_bytes"]-sqlite_used),
        "filesystem_paths_exposed":False,
        "caller_paths_accepted":False,
    }


def write_file(app_key:str,relative_path:str,data:bytes)->dict[str,Any]:
    if not isinstance(data,(bytes,bytearray)):
        raise AppResourceError("App storage writes require bytes.")
    root=files_root(app_key)
    target=_under(root,relative_path)
    if target.exists() and target.is_symlink():
        raise AppResourceError("App storage target may not be a symbolic link.")
    current_size=target.stat().st_size if target.is_file() else 0
    usage=_walk_usage(root)
    projected=usage-current_size+len(data)
    limit=_limits_from_app(homeserver_apps.get(app_key))["storage_limit_bytes"]
    if projected>limit:
        raise AppResourceError("App storage quota exceeded.",413)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+".tmp")
    temp.write_bytes(bytes(data))
    os.replace(temp,target)
    return {"relative_path":_safe_rel(relative_path).as_posix(),"size_bytes":len(data),"storage_used_bytes":projected}


def read_file(app_key:str,relative_path:str,max_bytes:int=1024*1024)->bytes:
    target=_under(files_root(app_key),relative_path)
    if not target.is_file() or target.is_symlink():
        raise AppResourceError("App storage file not found.",404)
    size=target.stat().st_size
    if size>max(1,min(int(max_bytes),16*1024*1024)):
        raise AppResourceError("App storage file exceeds the read limit.",413)
    return target.read_bytes()


def delete_file(app_key:str,relative_path:str)->bool:
    target=_under(files_root(app_key),relative_path)
    if not target.exists():
        return False
    if not target.is_file() or target.is_symlink():
        raise AppResourceError("App storage target is unavailable.",404)
    target.unlink()
    return True


def sqlite_path(app_key:str,name:str="app.db")->Path:
    candidate=str(name or "").strip()
    if not _SAFE_DB_NAME.fullmatch(candidate):
        raise AppResourceError("SQLite database name is invalid.")
    root=sqlite_root(app_key)
    target=(root/candidate).resolve()
    if root.resolve() not in target.parents:
        raise AppResourceError("SQLite path escaped its isolated root.")
    return target


def enforce_sqlite_quota(app_key:str)->dict[str,Any]:
    status=resource_status(app_key)
    if status["sqlite_used_bytes"]>status["sqlite_limit_bytes"]:
        raise AppResourceError("App SQLite quota exceeded.",413)
    return status


def purge_app_data(app_key:str)->None:
    app=homeserver_apps.get(app_key)
    root=_app_root(app["app_id"])
    if root.exists():
        shutil.rmtree(root)


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "isolated_data_root":True,
        "isolated_sqlite_root":True,
        "caller_filesystem_paths":False,
        "symbolic_links":False,
        "storage_quota":True,
        "sqlite_quota":True,
        "default_storage_limit_bytes":DEFAULT_STORAGE_LIMIT,
        "default_sqlite_limit_bytes":DEFAULT_SQLITE_LIMIT,
    }

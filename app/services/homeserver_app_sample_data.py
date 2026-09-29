from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import settings
from . import homeserver_apps

CONTRACT="vp3.app.sample-data.v1"
SETTINGS_CONTRACT="vp3.app.sample-data-settings.v1"


class AppSampleDataError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _settings_path()->Path:
    return settings.data_dir/"apps-platform"/"sample-data.json"


def _atomic_write(path:Path,payload:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    try:
        os.chmod(temp,0o600)
    except OSError:
        pass
    os.replace(temp,path)


def settings_status()->dict[str,Any]:
    path=_settings_path()
    enabled=False
    if path.is_file():
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload,dict):
                enabled=bool(payload.get("enabled",False))
        except (OSError,UnicodeDecodeError,json.JSONDecodeError):
            enabled=False
    return {
        "contract":SETTINGS_CONTRACT,
        "enabled":enabled,
        "default":"off",
        "production_data_mutated":False,
    }


def set_enabled(enabled:bool)->dict[str,Any]:
    payload={"contract":SETTINGS_CONTRACT,"enabled":bool(enabled)}
    _atomic_write(_settings_path(),payload)
    return settings_status()


def _active_content_root(app_key:str)->Path:
    app=homeserver_apps.get(app_key)
    release_id=str((app.get("metadata") or {}).get("active_release_id") or "")
    if not release_id.startswith("apprel_"):
        raise AppSampleDataError("App has no active release.",409)
    base=(settings.data_dir/"app-runtime"/app_key/"releases").resolve()
    root=(base/release_id/"content").resolve()
    if base not in root.parents or not root.is_dir():
        raise AppSampleDataError("Active app release is unavailable.",500)
    return root


def load_app_sample_data(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    root=_active_content_root(app_key)
    manifest_path=root/"vp3-app.json"
    try:
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppSampleDataError("Active app manifest is unavailable.",500) from exc
    relative=str(manifest.get("sample_data") or "").strip()
    state=settings_status()
    if not relative:
        return {
            "contract":CONTRACT,
            "app_key":app["app_key"],
            "available":False,
            "enabled":False,
            "items":[],
        }
    candidate=(root/relative).resolve()
    if root not in candidate.parents or not candidate.is_file() or candidate.is_symlink():
        raise AppSampleDataError("App sample data contract is unavailable.",500)
    try:
        payload=json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppSampleDataError("App sample data contract is invalid.",400) from exc
    if not isinstance(payload,dict) or payload.get("contract")!=CONTRACT:
        raise AppSampleDataError(f"App sample data contract must be {CONTRACT}.")
    items=payload.get("items",[])
    if not isinstance(items,list) or len(items)>500:
        raise AppSampleDataError("App sample data items are invalid.")
    normalized=[]
    for index,item in enumerate(items):
        if not isinstance(item,dict):
            raise AppSampleDataError(f"Sample data item {index+1} must be an object.")
        raw=json.dumps(item,separators=(",",":"),sort_keys=True)
        if len(raw.encode("utf-8"))>64*1024:
            raise AppSampleDataError("A sample data item exceeds the size limit.")
        normalized.append(item)
    return {
        "contract":CONTRACT,
        "app_key":app["app_key"],
        "available":True,
        "enabled":bool(state["enabled"]),
        "items":normalized if state["enabled"] else [],
        "item_count":len(normalized),
        "production_data_mutated":False,
    }


def public_capability()->dict[str,Any]:
    state=settings_status()
    return {
        "contract":CONTRACT,
        "admin_toggle":True,
        "global_enabled":state["enabled"],
        "default":"off",
        "separate_from_production_data":True,
        "max_items":500,
        "max_item_bytes":64*1024,
    }

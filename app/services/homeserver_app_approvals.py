from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings

CONTRACT="vp3.app.agent-approvals.v1"
_LOCK=threading.RLock()
_VALID_STATUS={"pending","executing","executed","denied","failed","expired"}


class AppApprovalStoreError(RuntimeError):
    pass


def _path()->Path:
    path=settings.data_dir/"apps-agent-approvals.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    return path


def _read()->dict[str,Any]:
    path=_path()
    if not path.is_file():
        return {"contract":CONTRACT,"requests":[]}
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppApprovalStoreError("Apps approval ledger is unreadable.") from exc
    if not isinstance(value,dict) or not isinstance(value.get("requests"),list):
        raise AppApprovalStoreError("Apps approval ledger is invalid.")
    return value


def _write(value:dict[str,Any])->None:
    path=_path()
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,separators=(",",":"),sort_keys=True)+"\n",encoding="utf-8")
    try:
        os.chmod(temp,0o600)
    except OSError:
        pass
    os.replace(temp,path)


def create(row:dict[str,Any])->dict[str,Any]:
    item=dict(row)
    if item.get("status") not in _VALID_STATUS:
        raise AppApprovalStoreError("Invalid Apps approval status.")
    with _LOCK:
        data=_read()
        if any(str(existing.get("id"))==str(item.get("id")) for existing in data["requests"]):
            raise AppApprovalStoreError("Duplicate Apps approval request.")
        data["requests"].append(item)
        _write(data)
    return dict(item)


def get(request_id:str)->dict[str,Any]|None:
    key=str(request_id or "").strip()
    with _LOCK:
        for row in _read()["requests"]:
            if str(row.get("id"))==key:
                return dict(row)
    return None


def list_rows(status:str|None=None,limit:int=100)->list[dict[str,Any]]:
    with _LOCK:
        rows=[dict(row) for row in _read()["requests"]]
    if status:
        rows=[row for row in rows if row.get("status")==status]
    rows.sort(key=lambda row:str(row.get("created_at") or ""),reverse=True)
    return rows[:max(1,min(500,int(limit)))]


def update_if_status(request_id:str,expected_status:str,**changes:Any)->dict[str,Any]|None:
    key=str(request_id or "").strip()
    with _LOCK:
        data=_read()
        for index,row in enumerate(data["requests"]):
            if str(row.get("id"))!=key or row.get("status")!=expected_status:
                continue
            item=dict(row)
            for name,value in changes.items():
                if name=="status" and value not in _VALID_STATUS:
                    raise AppApprovalStoreError("Invalid Apps approval status.")
                item[name]=value
            data["requests"][index]=item
            _write(data)
            return dict(item)
    return None


def expire_pending(now_iso:str)->int:
    changed=0
    with _LOCK:
        data=_read()
        rows=[]
        for row in data["requests"]:
            item=dict(row)
            if item.get("status")=="pending" and str(item.get("expires_at") or "")<=now_iso:
                item["status"]="expired"
                item["decided_at"]=datetime.now(timezone.utc).isoformat()
                item["error"]="Approval request expired."
                changed+=1
            rows.append(item)
        if changed:
            data["requests"]=rows
            _write(data)
    return changed


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "durable":True,
        "atomic_write":True,
        "owner_approval_flow":True,
        "database_schema_change_required":False,
    }

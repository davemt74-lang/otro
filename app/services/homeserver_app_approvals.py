from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db

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


def _decode(row:dict[str,Any])->dict[str,Any]:
    item=dict(row)
    try:
        item["arguments"]=json.loads(item.pop("arguments_json","{}") or "{}")
    except json.JSONDecodeError:
        item["arguments"]={}
    try:
        item["arguments_meta"]=json.loads(item.pop("arguments_meta_json","{}") or "{}")
    except json.JSONDecodeError:
        item["arguments_meta"]={}
    return item


def approve(request_id:str)->dict[str,Any]:
    from . import tools

    row=get(request_id)
    if row is None:
        raise AppApprovalStoreError("Apps approval request not found.")
    if row.get("status")!="pending":
        raise AppApprovalStoreError(f"Apps approval request is already {row.get('status')}.")
    reserved=update_if_status(request_id,"pending",status="executing",decided_at=datetime.now(timezone.utc).isoformat())
    if reserved is None:
        raise AppApprovalStoreError("Apps approval request is no longer pending.")
    item=_decode(reserved)
    issue_key=(item.get("arguments_meta") or {}).get("maintenance_issue_key")
    if issue_key is not None:
        from . import maintenance_conversation
        try:
            maintenance_conversation.validate_execution(
                issue_key,str(item.get("action_key") or ""),dict(item.get("arguments") or {}),
            )
        except maintenance_conversation.MaintenanceError as exc:
            reason="Maintenance issue changed or resolved; obtain new diagnostics."
            update_if_status(
                request_id,"executing",status="denied",error=reason,
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            with db() as connection:
                connection.execute(
                    "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES ('owner','control-center','action.denied','action_request',?,?)",
                    (request_id,json.dumps({"homeserver_app":True,"reason":"stale_health_issue"},separators=(",",":"))),
                )
            raise AppApprovalStoreError(reason) from exc
    try:
        execution=tools.execute_tool(
            str(item.get("source_app_key") or "owner"),
            str(item.get("action_key") or ""),
            dict(item.get("arguments") or {}),
            set(),
            owner=True,
        )
    except tools.ToolError as exc:
        update_if_status(
            request_id,"executing",status="failed",error=str(exc)[:1000],
            executed_at=datetime.now(timezone.utc).isoformat(),
        )
        with db() as connection:
            connection.execute(
                "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES ('owner','control-center','action.failed','action_request',?,?)",
                (request_id,json.dumps({"homeserver_app":True,"error":str(exc)[:240]},separators=(",",":"))),
            )
        raise AppApprovalStoreError(f"Approved Apps action could not execute: {exc}") from exc

    updated=update_if_status(
        request_id,"executing",status="executed",execution_tool_run_id=execution["run_id"],
        error=None,executed_at=datetime.now(timezone.utc).isoformat(),
    )
    with db() as connection:
        connection.execute(
            "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES ('owner','control-center','action.approved','action_request',?,?)",
            (request_id,json.dumps({"homeserver_app":True,"execution_tool_run_id":execution["run_id"]},separators=(",",":"))),
        )
    return _decode(updated or get(request_id) or {})


def deny(request_id:str)->dict[str,Any]:
    row=get(request_id)
    if row is None:
        raise AppApprovalStoreError("Apps approval request not found.")
    if row.get("status")!="pending":
        raise AppApprovalStoreError(f"Apps approval request is already {row.get('status')}.")
    updated=update_if_status(
        request_id,"pending",status="denied",decided_at=datetime.now(timezone.utc).isoformat(),error=None
    )
    if updated is None:
        raise AppApprovalStoreError("Apps approval request is no longer pending.")
    with db() as connection:
        connection.execute(
            "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES ('owner','control-center','action.denied','action_request',?,?)",
            (request_id,json.dumps({"homeserver_app":True},separators=(",",":"))),
        )
    return _decode(updated)


def cancel_pending(request_ids:list[str]|tuple[str,...],*,source_app_key:str|None=None,reason:str="Interrupted Agent turn cancelled this pending action.")->int:
    cancelled=0
    for raw in request_ids:
        request_id=str(raw or "").strip()
        if not request_id:
            continue
        row=get(request_id)
        if row is None:
            continue
        if source_app_key and str(row.get("source_app_key") or "")!=str(source_app_key).strip():
            continue
        updated=update_if_status(
            request_id,"pending",status="denied",decided_at=datetime.now(timezone.utc).isoformat(),error=reason[:1000]
        )
        if updated is None:
            continue
        cancelled+=1
        with db() as connection:
            connection.execute(
                "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES ('system','agent-cancellation','action.cancelled','action_request',?,?)",
                (request_id,json.dumps({"homeserver_app":True,"reason":reason[:240]},separators=(",",":"))),
            )
    return cancelled

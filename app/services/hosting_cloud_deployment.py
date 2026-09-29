from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from . import hosting_cloud_control, hosting_deployment, hosting_recovery, hosting_runtime, hosting_sqlite

CONTRACT="vp3.hosting.cloud-deployment.v1"
_TRANSFER_ID=re.compile(r"^transfer_[a-z0-9]{24}$")
_RELEASE_ID=re.compile(r"^release_[0-9a-f]{24}$")
MAX_CHUNK_BYTES=128*1024
_OP_LOCK_GUARD=threading.RLock()
_OP_LOCKS:dict[str,threading.RLock]={}


def _operation_lock(site_id:str)->threading.RLock:
    with _OP_LOCK_GUARD:
        lock=_OP_LOCKS.get(site_id)
        if lock is None:
            lock=threading.RLock()
            _OP_LOCKS[site_id]=lock
        return lock



class CloudDeploymentError(hosting_runtime.HostingError):
    pass


def _transfer_root(site_id:str)->Path:
    root=hosting_runtime.site_root(site_id)/"cloud-transfers"
    root.mkdir(parents=True,exist_ok=True)
    hosting_runtime._ensure_no_symlink(root)
    return root


def _meta_path(site_id:str,transfer_id:str)->Path:
    if not _TRANSFER_ID.fullmatch(str(transfer_id or "")):
        raise CloudDeploymentError("Invalid deployment transfer identifier.")
    return _transfer_root(site_id)/f"{transfer_id}.json"


def _data_path(site_id:str,transfer_id:str)->Path:
    if not _TRANSFER_ID.fullmatch(str(transfer_id or "")):
        raise CloudDeploymentError("Invalid deployment transfer identifier.")
    return _transfer_root(site_id)/f"{transfer_id}.zip.part"


def _load(site_id:str,transfer_id:str)->dict[str,Any]:
    path=_meta_path(site_id,transfer_id)
    if not path.is_file():
        raise CloudDeploymentError("Deployment transfer not found.",404)
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise CloudDeploymentError("Deployment transfer metadata is unreadable.",500) from exc
    if not isinstance(payload,dict) or payload.get("site_id")!=site_id or payload.get("transfer_id")!=transfer_id:
        raise CloudDeploymentError("Deployment transfer metadata is invalid.",500)
    return payload


def _write(site_id:str,payload:dict[str,Any])->None:
    path=_meta_path(site_id,str(payload["transfer_id"]))
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def _site_for_cloud(cloud_site_id:str)->tuple[dict[str,Any],dict[str,Any]]:
    projection=hosting_cloud_control.status(cloud_site_id)
    site_id=str(projection["site_id"])
    binding=hosting_cloud_control.binding_for_site(site_id)
    if not binding:
        raise CloudDeploymentError("Cloud hosting binding not found.",404)
    return hosting_runtime.get_site(site_id),binding


def begin(
    cloud_site_id:str,
    *,
    revision:int,
    package_sha256:str,
    package_bytes:int,
    request_key:str,
)->dict[str,Any]:
    site,binding=_site_for_cloud(cloud_site_id)
    if int(revision)!=int(binding.get("revision") or 0):
        raise CloudDeploymentError("Deployment revision does not match current Cloud desired-state revision.",409)
    digest=str(package_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}",digest):
        raise CloudDeploymentError("package_sha256 is invalid.")
    size=int(package_bytes)
    if size<1 or size>hosting_deployment.MAX_PACKAGE_BYTES:
        raise CloudDeploymentError("Deployment package size is outside the allowed range.",413)
    key=str(request_key or "").strip()
    if not key or len(key)>160:
        raise CloudDeploymentError("request_key is required and must be at most 160 characters.")

    root=_transfer_root(str(site["site_id"]))
    for meta in root.glob("transfer_*.json"):
        try:
            payload=json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("request_key")==key:
            if (
                payload.get("package_sha256")==digest
                and int(payload.get("package_bytes") or 0)==size
                and int(payload.get("revision") or 0)==int(revision)
            ):
                return status(cloud_site_id,str(payload["transfer_id"]))
            raise CloudDeploymentError("Deployment request key was already used for a different package or revision.",409)

    transfer_id="transfer_"+uuid.uuid4().hex[:24]
    payload={
        "contract":CONTRACT,
        "transfer_id":transfer_id,
        "cloud_site_id":cloud_site_id,
        "site_id":site["site_id"],
        "revision":int(revision),
        "package_sha256":digest,
        "package_bytes":size,
        "request_key":key,
        "received_bytes":0,
        "next_chunk":0,
        "chunk_sha256":[],
        "state":"receiving",
        "release_id":None,
        "error":None,
    }
    _write(str(site["site_id"]),payload)
    _data_path(str(site["site_id"]),transfer_id).write_bytes(b"")
    return _projection(payload)


def append_chunk(cloud_site_id:str,transfer_id:str,chunk_index:int,data_b64:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    payload=_load(site_id,transfer_id)
    if payload.get("cloud_site_id")!=cloud_site_id:
        raise CloudDeploymentError("Deployment transfer does not belong to this Cloud site.",409)
    if payload.get("state")!="receiving":
        return _projection(payload)
    index=int(chunk_index)
    expected=int(payload.get("next_chunk") or 0)
    if index<expected:
        hashes=payload.get("chunk_sha256") or []
        if index>=len(hashes):
            raise CloudDeploymentError("Deployment retry cannot be verified.",409)
        try:
            retry_data=base64.b64decode(str(data_b64 or "").strip(),validate=True)
        except (binascii.Error,ValueError) as exc:
            raise CloudDeploymentError("Deployment chunk is not valid base64.") from exc
        if hashlib.sha256(retry_data).hexdigest()!=str(hashes[index]):
            raise CloudDeploymentError("Deployment chunk retry does not match the previously accepted chunk.",409)
        return _projection(payload)
    if index!=expected:
        raise CloudDeploymentError("Deployment chunk is out of order.",409)
    encoded=str(data_b64 or "").strip()
    try:
        data=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError) as exc:
        raise CloudDeploymentError("Deployment chunk is not valid base64.") from exc
    if not data or len(data)>MAX_CHUNK_BYTES:
        raise CloudDeploymentError("Deployment chunk exceeds the transfer chunk limit.",413)
    received=int(payload.get("received_bytes") or 0)+len(data)
    if received>int(payload["package_bytes"]):
        raise CloudDeploymentError("Deployment transfer exceeds its declared size.",409)
    path=_data_path(site_id,transfer_id)
    if path.is_symlink():
        raise CloudDeploymentError("Deployment transfer storage is unsafe.",409)
    if int(payload.get("received_bytes") or 0)>0 and not path.is_file():
        raise CloudDeploymentError("Deployment transfer storage is missing and cannot resume.",409)
    with path.open("ab") as handle:
        handle.write(data)
    payload["received_bytes"]=received
    hashes=list(payload.get("chunk_sha256") or [])
    hashes.append(hashlib.sha256(data).hexdigest())
    payload["chunk_sha256"]=hashes
    payload["next_chunk"]=expected+1
    _write(site_id,payload)
    return _projection(payload)


def commit(cloud_site_id:str,transfer_id:str)->dict[str,Any]:
    site,binding=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    payload=_load(site_id,transfer_id)
    if payload.get("cloud_site_id")!=cloud_site_id:
        raise CloudDeploymentError("Deployment transfer does not belong to this Cloud site.",409)
    if int(payload.get("revision") or 0)!=int(binding.get("revision") or 0):
        raise CloudDeploymentError("Deployment transfer is stale relative to Cloud desired state.",409)
    if payload.get("state")=="applied":
        return _projection(payload)
    if payload.get("state")!="receiving":
        raise CloudDeploymentError("Deployment transfer is not committable.",409)
    if int(payload.get("received_bytes") or 0)!=int(payload.get("package_bytes") or 0):
        raise CloudDeploymentError("Deployment transfer is incomplete.",409)
    data_path=_data_path(site_id,transfer_id)
    if not data_path.is_file() or data_path.is_symlink():
        raise CloudDeploymentError("Deployment transfer storage is missing or unsafe.",409)
    package=data_path.read_bytes()
    digest=hashlib.sha256(package).hexdigest()
    if digest!=payload.get("package_sha256"):
        payload["state"]="failed"
        payload["error"]="checksum_mismatch"
        _write(site_id,payload)
        raise CloudDeploymentError("Deployment transfer checksum verification failed.",409)
    try:
        release=hosting_deployment.deploy_package(site_id,package,request_key=str(payload["request_key"]))
        payload["state"]="applied"
        payload["release_id"]=release["release_id"]
        payload["pre_deploy_recovery_id"]=release.get("pre_deploy_recovery_id")
        payload["sqlite_migrations"]=(release.get("sqlite_migrations") or {}).get("applied",[])
        payload["sqlite_migration_recovery_id"]=(release.get("sqlite_migrations") or {}).get("recovery_id")
        payload["error"]=None
        _write(site_id,payload)
        data_path.unlink(missing_ok=True)
        return _projection(payload)
    except hosting_runtime.HostingError as exc:
        payload["state"]="failed"
        payload["error"]=str(exc)[:240]
        _write(site_id,payload)
        raise CloudDeploymentError(str(exc),exc.status_code) from exc


def rollback(cloud_site_id:str,*,request_key:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    key=str(request_key or "").strip()
    if not key or len(key)>160:
        raise CloudDeploymentError("request_key is required and must be at most 160 characters.")
    replay_path=_transfer_root(site_id)/("rollback_"+hashlib.sha256(key.encode("utf-8")).hexdigest()+".json")
    if replay_path.is_file():
        try:
            saved=json.loads(replay_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CloudDeploymentError("Rollback acknowledgement is unreadable.",500) from exc
        if saved.get("cloud_site_id")!=cloud_site_id or saved.get("request_key")!=key:
            raise CloudDeploymentError("Rollback request key conflicts with existing acknowledgement.",409)
        return saved
    try:
        release=hosting_deployment.rollback(site_id)
    except hosting_runtime.HostingError as exc:
        raise CloudDeploymentError(str(exc),exc.status_code) from exc
    result={
        "contract":CONTRACT,
        "cloud_site_id":cloud_site_id,
        "site_id":site_id,
        "operation":"rollback",
        "request_key":key,
        "state":"applied",
        "release_id":release.get("release_id"),
        "previous_release_id":release.get("previous_release_id"),
        "recovery":hosting_recovery.recovery_health(site_id),
        "sqlite":hosting_sqlite.schema_status(site_id),
    }
    tmp=replay_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,replay_path)
    return result


def releases(cloud_site_id:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    deployment=hosting_deployment.deployment_status(site_id)
    items=[]
    for item in hosting_deployment.list_releases(site_id):
        items.append({
            "release_id":str(item.get("release_id") or ""),
            "app_version":str(item.get("app_version") or ""),
            "runtime":str(item.get("runtime") or ""),
            "entrypoint":str(item.get("entrypoint") or ""),
            "package_sha256":str(item.get("package_sha256") or ""),
            "created_at":str(item.get("created_at") or ""),
            "active":bool(item.get("active")),
            "previous":bool(item.get("previous")),
        })
    return {
        "contract":CONTRACT,
        "cloud_site_id":cloud_site_id,
        "site_id":site_id,
        "active_release_id":deployment.get("active_release_id"),
        "previous_release_id":deployment.get("previous_release_id"),
        "releases":items,
    }


def promote(cloud_site_id:str,release_id:str,*,request_key:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    with _operation_lock(site_id):
        return _promote_locked(cloud_site_id,release_id,request_key=request_key)


def _promote_locked(cloud_site_id:str,release_id:str,*,request_key:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    release=str(release_id or "").strip()
    if not _RELEASE_ID.fullmatch(release):
        raise CloudDeploymentError("release_id is invalid.")
    key=str(request_key or "").strip()
    if not key or len(key)>160:
        raise CloudDeploymentError("request_key is required and must be at most 160 characters.")
    replay_path=_transfer_root(site_id)/("promote_"+hashlib.sha256(key.encode("utf-8")).hexdigest()+".json")
    if replay_path.is_file():
        try:
            saved=json.loads(replay_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CloudDeploymentError("Release promotion acknowledgement is unreadable.",500) from exc
        if saved.get("cloud_site_id")!=cloud_site_id or saved.get("request_key")!=key or saved.get("release_id")!=release:
            raise CloudDeploymentError("Release promotion request key conflicts with existing acknowledgement.",409)
        return saved
    try:
        promoted=hosting_deployment.promote_release(site_id,release)
    except hosting_runtime.HostingError as exc:
        raise CloudDeploymentError(str(exc),exc.status_code) from exc
    result={
        "contract":CONTRACT,
        "cloud_site_id":cloud_site_id,
        "site_id":site_id,
        "operation":"promote",
        "request_key":key,
        "state":"applied",
        "release_id":promoted.get("release_id"),
        "previous_release_id":promoted.get("previous_release_id"),
        "pre_promote_recovery_id":promoted.get("pre_promote_recovery_id"),
        "replayed":bool(promoted.get("replayed")),
        "recovery":hosting_recovery.recovery_health(site_id),
        "sqlite":hosting_sqlite.schema_status(site_id),
    }
    tmp=replay_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,replay_path)
    return result


def prune(cloud_site_id:str,keep:int,*,request_key:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    with _operation_lock(site_id):
        return _prune_locked(cloud_site_id,keep,request_key=request_key)


def _prune_locked(cloud_site_id:str,keep:int,*,request_key:str)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    key=str(request_key or "").strip()
    if not key or len(key)>160:
        raise CloudDeploymentError("request_key is required and must be at most 160 characters.")
    retain=max(2,min(50,int(keep)))
    replay_path=_transfer_root(site_id)/("prune_"+hashlib.sha256(key.encode("utf-8")).hexdigest()+".json")
    if replay_path.is_file():
        try:
            saved=json.loads(replay_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CloudDeploymentError("Release retention acknowledgement is unreadable.",500) from exc
        if saved.get("cloud_site_id")!=cloud_site_id or saved.get("request_key")!=key or int(saved.get("keep") or 0)!=retain:
            raise CloudDeploymentError("Release retention request key conflicts with existing acknowledgement.",409)
        return saved
    try:
        pruned=hosting_deployment.prune_releases(site_id,retain)
    except hosting_runtime.HostingError as exc:
        raise CloudDeploymentError(str(exc),exc.status_code) from exc
    result={
        "contract":CONTRACT,
        "cloud_site_id":cloud_site_id,
        "site_id":site_id,
        "operation":"prune",
        "request_key":key,
        "state":"applied",
        "keep":retain,
        "deleted_release_ids":list(pruned.get("deleted_release_ids") or []),
        "active_release_id":pruned.get("active_release_id"),
        "previous_release_id":pruned.get("previous_release_id"),
        "releases":[{
            "release_id":str(item.get("release_id") or ""),
            "app_version":str(item.get("app_version") or ""),
            "runtime":str(item.get("runtime") or ""),
            "package_sha256":str(item.get("package_sha256") or ""),
            "created_at":str(item.get("created_at") or ""),
            "active":bool(item.get("active")),
            "previous":bool(item.get("previous")),
        } for item in (pruned.get("releases") or [])],
    }
    tmp=replay_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,replay_path)
    return result


def status(cloud_site_id:str,transfer_id:str|None=None)->dict[str,Any]:
    site,_=_site_for_cloud(cloud_site_id)
    site_id=str(site["site_id"])
    if transfer_id:
        payload=_load(site_id,transfer_id)
        if payload.get("cloud_site_id")!=cloud_site_id:
            raise CloudDeploymentError("Deployment transfer does not belong to this Cloud site.",409)
        return _projection(payload)
    items=[]
    for meta in _transfer_root(site_id).glob("transfer_*.json"):
        try:
            payload=json.loads(meta.read_text(encoding="utf-8"))
            if payload.get("cloud_site_id")==cloud_site_id:
                items.append(_projection(payload))
        except Exception:
            continue
    items.sort(key=lambda item:item["transfer_id"],reverse=True)
    deployment=hosting_deployment.deployment_status(site_id)
    return {
        "contract":CONTRACT,
        "cloud_site_id":cloud_site_id,
        "site_id":site_id,
        "active_release_id":deployment.get("active_release_id"),
        "transfers":items[:20],
        "recovery":hosting_recovery.recovery_health(site_id),
        "sqlite":hosting_sqlite.schema_status(site_id),
    }


def _projection(payload:dict[str,Any])->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "transfer_id":payload["transfer_id"],
        "cloud_site_id":payload["cloud_site_id"],
        "site_id":payload["site_id"],
        "revision":payload["revision"],
        "state":payload["state"],
        "package_bytes":payload["package_bytes"],
        "received_bytes":payload["received_bytes"],
        "next_chunk":payload["next_chunk"],
        "release_id":payload.get("release_id"),
        "pre_deploy_recovery_id":payload.get("pre_deploy_recovery_id"),
        "sqlite_migrations":payload.get("sqlite_migrations") or [],
        "sqlite_migration_recovery_id":payload.get("sqlite_migration_recovery_id"),
        "error":payload.get("error"),
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "chunked_transfer":True,
        "max_chunk_bytes":MAX_CHUNK_BYTES,
        "max_package_bytes":hosting_deployment.MAX_PACKAGE_BYTES,
        "transfer_resume":True,
        "request_idempotency":True,
        "checksum_verification":True,
        "desired_revision_gate":True,
        "deployment_acknowledgement":True,
        "rollback_acknowledgement":True,
        "release_history":True,
        "release_promotion":True,
        "release_retention":True,
        "promotion_acknowledgement":True,
        "retention_acknowledgement":True,
        "serialized_release_operations":True,
        "recovery_health":True,
        "sqlite_migration_status":True,
        "homeserver_authoritative_execution":True,
        "cloud_filesystem_access":False,
        "cloud_raw_sql":False,
    }

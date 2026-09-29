from __future__ import annotations

import ctypes
import json
import os
import re
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from . import homeserver_apps
from .pairing import DEFAULT_PERMISSIONS

CONTRACT="vp3.app.security.v1"
EXTRA_PERMISSIONS={
    "agent.context",
    "hardware.camera",
    "hardware.microphone",
    "network.external",
    "notifications.write",
}
ALLOWED_PERMISSIONS=set(DEFAULT_PERMISSIONS)|EXTRA_PERMISSIONS
_SECRET_KEY=re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")
CRYPTPROTECT_UI_FORBIDDEN=0x1


class AppSecurityError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


class _DataBlob(ctypes.Structure):
    _fields_=[("cbData",wintypes.DWORD),("pbData",ctypes.POINTER(ctypes.c_ubyte))]


def _windows_libraries():
    crypt32=ctypes.WinDLL("crypt32",use_last_error=True)
    kernel32=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel32.LocalFree.argtypes=[ctypes.c_void_p]
    kernel32.LocalFree.restype=ctypes.c_void_p
    return crypt32,kernel32


def _free_blob(kernel32,blob:_DataBlob)->None:
    if blob.pbData:
        kernel32.LocalFree(ctypes.cast(blob.pbData,ctypes.c_void_p))
        blob.pbData=ctypes.POINTER(ctypes.c_ubyte)()
        blob.cbData=0


def _protect_windows(data:bytes,description:str)->bytes:
    crypt32,kernel32=_windows_libraries()
    source_buffer=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    source=_DataBlob(len(data),ctypes.cast(source_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=_DataBlob()
    crypt32.CryptProtectData.argtypes=[
        ctypes.POINTER(_DataBlob),wintypes.LPCWSTR,ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype=wintypes.BOOL
    try:
        if not crypt32.CryptProtectData(
            ctypes.byref(source),description,None,None,None,
            CRYPTPROTECT_UI_FORBIDDEN,ctypes.byref(output),
        ):
            raise OSError(ctypes.get_last_error(),"CryptProtectData failed")
        return ctypes.string_at(output.pbData,output.cbData)
    finally:
        ctypes.memset(source_buffer,0,len(data))
        _free_blob(kernel32,output)


def _unprotect_windows(data:bytes)->bytes:
    crypt32,kernel32=_windows_libraries()
    source_buffer=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    source=_DataBlob(len(data),ctypes.cast(source_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=_DataBlob()
    crypt32.CryptUnprotectData.argtypes=[
        ctypes.POINTER(_DataBlob),ctypes.POINTER(wintypes.LPWSTR),ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype=wintypes.BOOL
    try:
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source),None,None,None,None,
            CRYPTPROTECT_UI_FORBIDDEN,ctypes.byref(output),
        ):
            raise OSError(ctypes.get_last_error(),"CryptUnprotectData failed")
        return ctypes.string_at(output.pbData,output.cbData)
    finally:
        ctypes.memset(source_buffer,0,len(data))
        _free_blob(kernel32,output)


def _vault_path(app_id:str)->Path:
    return settings.data_dir/"security"/"apps"/f"{app_id}.dat"


def _encode(data:dict[str,str],app_id:str)->bytes:
    raw=json.dumps(data,separators=(",",":"),sort_keys=True).encode("utf-8")
    return _protect_windows(raw,f"HomeServer app secrets {app_id}") if os.name=="nt" else raw


def _decode(payload:bytes)->dict[str,str]:
    raw=_unprotect_windows(payload) if os.name=="nt" else payload
    parsed=json.loads(raw.decode("utf-8"))
    if not isinstance(parsed,dict):
        raise AppSecurityError("App secret vault is invalid.",500)
    out={}
    for key,value in parsed.items():
        name=str(key or "").strip()
        secret=str(value or "")
        if _SECRET_KEY.fullmatch(name) and secret:
            out[name]=secret
    return out


def _atomic_write(path:Path,payload:bytes)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    with temp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temp,0o600)
    except OSError:
        pass
    os.replace(temp,path)


def _load_vault(app_id:str)->dict[str,str]:
    path=_vault_path(app_id)
    if not path.is_file():
        return {}
    try:
        return _decode(path.read_bytes())
    except Exception as exc:
        raise AppSecurityError("App secrets could not be decrypted on this device.",500) from exc


def _save_vault(app_id:str,values:dict[str,str])->None:
    path=_vault_path(app_id)
    if values:
        _atomic_write(path,_encode(values,app_id))
    else:
        path.unlink(missing_ok=True)


def _metadata(app:dict[str,Any])->dict[str,Any]:
    return dict(app.get("metadata") or {})


def _write_metadata(app:dict[str,Any],metadata:dict[str,Any],event_type:str,event_meta:dict[str,Any])->None:
    with db() as connection:
        connection.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?",
            (json.dumps(metadata,separators=(",",":"),sort_keys=True),app["app_id"]),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?,?, 'owner','local_owner',?)""",
            (app["app_id"],event_type,json.dumps(event_meta,separators=(",",":"),sort_keys=True)),
        )


def normalize_declared_permissions(values:list[str]|None)->list[str]:
    if not isinstance(values,list):
        raise AppSecurityError("App permissions must be a list.")
    output=[]
    for raw in values:
        item=str(raw or "").strip()
        if item not in ALLOWED_PERMISSIONS:
            raise AppSecurityError(f"Unsupported app permission: {item or '<empty>'}.")
        if item not in output:
            output.append(item)
    return sorted(output)


def sync_declared_permissions(app_key:str,permissions:list[str])->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    declared=normalize_declared_permissions(permissions)
    metadata=_metadata(app)
    security=dict(metadata.get("security") or {})
    previous_declared=[str(x) for x in security.get("declared_permissions",[]) if str(x)]
    grants={str(k):bool(v) for k,v in dict(security.get("permission_grants") or {}).items()}
    grants={permission:bool(grants.get(permission,False)) for permission in declared}
    security["declared_permissions"]=declared
    security["permission_grants"]=grants
    security["permissions_updated_at"]=datetime.now(timezone.utc).isoformat()
    metadata["security"]=security
    added=sorted(set(declared)-set(previous_declared))
    removed=sorted(set(previous_declared)-set(declared))
    _write_metadata(app,metadata,"app.permissions.synced",{"added":added,"removed":removed,"new_permissions_default_denied":True})
    return permission_status(app_key)


def permission_status(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    security=dict(_metadata(app).get("security") or {})
    declared=sorted({str(x) for x in security.get("declared_permissions",[]) if str(x)})
    grants={str(k):bool(v) for k,v in dict(security.get("permission_grants") or {}).items()}
    return {
        "contract":CONTRACT,
        "app_key":app["app_key"],
        "permissions":[{"permission":p,"allowed":bool(grants.get(p,False))} for p in declared],
        "declared_count":len(declared),
        "allowed_count":sum(1 for p in declared if grants.get(p,False)),
        "default_for_new_permissions":"denied",
    }


def set_permission(app_key:str,permission:str,allowed:bool)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    name=str(permission or "").strip()
    security=dict(_metadata(app).get("security") or {})
    declared=sorted({str(x) for x in security.get("declared_permissions",[]) if str(x)})
    if name not in declared:
        raise AppSecurityError("Permission is not declared by this app.",409)
    grants={str(k):bool(v) for k,v in dict(security.get("permission_grants") or {}).items()}
    grants[name]=bool(allowed)
    security["permission_grants"]=grants
    metadata=_metadata(app)
    metadata["security"]=security
    _write_metadata(app,metadata,"app.permission.updated",{"permission":name,"allowed":bool(allowed)})
    return permission_status(app_key)


def permission_allowed(app_key:str,permission:str)->bool:
    status=permission_status(app_key)
    return any(row["permission"]==permission and row["allowed"] for row in status["permissions"])


def set_secret(app_key:str,key:str,value:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    name=str(key or "").strip().upper()
    secret=str(value or "")
    if not _SECRET_KEY.fullmatch(name):
        raise AppSecurityError("Secret key must use uppercase letters, numbers, and underscores.")
    if not secret or len(secret)>16000:
        raise AppSecurityError("Secret value is required and must be at most 16000 characters.")
    values=_load_vault(app["app_id"])
    values[name]=secret
    _save_vault(app["app_id"],values)
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.secret.updated','owner','local_owner',?)""",
            (app["app_id"],json.dumps({"key":name},separators=(",",":"))),
        )
    return secret_status(app_key)


def remove_secret(app_key:str,key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    name=str(key or "").strip().upper()
    if not _SECRET_KEY.fullmatch(name):
        raise AppSecurityError("Secret key is invalid.")
    values=_load_vault(app["app_id"])
    existed=name in values
    values.pop(name,None)
    _save_vault(app["app_id"],values)
    if existed:
        with db() as connection:
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.secret.removed','owner','local_owner',?)""",
                (app["app_id"],json.dumps({"key":name},separators=(",",":"))),
            )
    return secret_status(app_key)


def get_secret(app_key:str,key:str)->str|None:
    app=homeserver_apps.get(app_key)
    name=str(key or "").strip().upper()
    if not _SECRET_KEY.fullmatch(name):
        raise AppSecurityError("Secret key is invalid.")
    return _load_vault(app["app_id"]).get(name)


def secret_status(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    values=_load_vault(app["app_id"])
    return {
        "contract":CONTRACT,
        "app_key":app["app_key"],
        "configured_keys":sorted(values),
        "count":len(values),
        "values_exposed":False,
        "protection":"windows-dpapi" if os.name=="nt" else "restricted-local-file",
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "declared_permissions":True,
        "owner_approval_required":True,
        "new_permissions_default_denied":True,
        "permission_expansion_auto_approved":False,
        "write_only_secret_api":True,
        "secret_values_exposed":False,
        "secret_protection":"windows-dpapi" if os.name=="nt" else "restricted-local-file",
        "allowed_permissions":sorted(ALLOWED_PERMISSIONS),
    }

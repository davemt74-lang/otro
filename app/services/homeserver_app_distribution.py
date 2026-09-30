from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from ..services.owner_secret import load_or_create_owner_secret
from . import homeserver_app_packages, homeserver_app_sdk, homeserver_app_security, homeserver_apps

CONTRACT="vp3.app.distribution.v1"
BUNDLE_CONTRACT="vp3.app.distribution-bundle.v1"
MAX_BUNDLE_BYTES=homeserver_app_packages.MAX_PACKAGE_BYTES+4*1024*1024


class AppDistributionError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _owner_secret_bytes()->bytes:
    return load_or_create_owner_secret().encode("utf-8")


def publisher_fingerprint()->str:
    return hashlib.sha256(_owner_secret_bytes()).hexdigest()[:32]


def _canonical(value:dict[str,Any])->bytes:
    return json.dumps(value,separators=(",",":"),sort_keys=True,ensure_ascii=False).encode("utf-8")


def _seal(value:dict[str,Any])->str:
    return hmac.new(_owner_secret_bytes(),_canonical(value),hashlib.sha256).hexdigest()


def _zip_project_or_active_release(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app["app_class"]!="user" or app["protected_system_app"]:
        raise AppDistributionError("Only user-created apps can be distributed.",409)
    project=homeserver_app_sdk.app_root(app_key)
    if project.is_dir():
        try:
            built=homeserver_app_packages.build_project_package(app_key)
            return {"package":built["package"],"validation":built["validation"],"source":"project"}
        except Exception:
            pass
    try:
        root=homeserver_app_packages.active_content_root(app_key)
    except homeserver_app_packages.AppPackageError as exc:
        raise AppDistributionError("This app has no distributable project or active release.",409) from exc
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        count=0
        expanded=0
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise AppDistributionError("Active release contains a symbolic link.",409)
            if path.is_dir():
                continue
            rel=path.relative_to(root).as_posix()
            homeserver_app_packages._safe_rel(rel)
            size=path.stat().st_size
            count+=1
            expanded+=size
            if count>homeserver_app_packages.MAX_FILES:
                raise AppDistributionError("App release contains too many files.",413)
            if expanded>homeserver_app_packages.MAX_UNCOMPRESSED_BYTES:
                raise AppDistributionError("App release exceeds the expanded size limit.",413)
            archive.write(path,rel)
    package=buffer.getvalue()
    validation=homeserver_app_packages.validate_package(package,expected_app_key=app_key)
    return {"package":package,"validation":validation,"source":"active_release"}


def distribution_descriptor(app_key:str)->dict[str,Any]:
    built=_zip_project_or_active_release(app_key)
    app=homeserver_apps.get(app_key)
    manifest=dict(built["validation"]["manifest"])
    descriptor={
        "contract":CONTRACT,
        "distribution_id":"appdist_"+uuid.uuid4().hex,
        "app_key":app["app_key"],
        "name":app["name"],
        "version":str(manifest.get("version") or ""),
        "runtime":str(manifest.get("runtime") or ""),
        "sdk_version":str(manifest.get("sdk_version") or ""),
        "release_channel":str(manifest.get("release_channel") or "stable"),
        "package_sha256":str(built["validation"]["package_sha256"]),
        "compressed_bytes":int(built["validation"]["compressed_bytes"]),
        "expanded_bytes":int(built["validation"]["expanded_bytes"]),
        "file_count":int(built["validation"]["file_count"]),
        "permissions":list(manifest.get("permissions") or []),
        "data_schema_version":str(manifest.get("data_schema_version") or "1"),
        "source_kind":built["source"],
        "publisher_fingerprint":publisher_fingerprint(),
        "includes_app_data":False,
        "includes_secrets":False,
        "ownership_transfer":False,
    }
    descriptor["publisher_seal"]=_seal(descriptor)
    return {"descriptor":descriptor,"package":built["package"],"validation":built["validation"]}


def export_bundle(app_key:str)->dict[str,Any]:
    exported=distribution_descriptor(app_key)
    descriptor=dict(exported["descriptor"])
    bundle_meta={
        "contract":BUNDLE_CONTRACT,
        "descriptor":descriptor,
    }
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-distribution.json",json.dumps(bundle_meta,indent=2,sort_keys=True)+"\n")
        archive.writestr("app-package.zip",exported["package"])
    bundle=buffer.getvalue()
    if len(bundle)>MAX_BUNDLE_BYTES:
        raise AppDistributionError("Distribution bundle exceeds the size limit.",413)
    safe_version="".join(ch if ch.isalnum() or ch in {".","-","_"} else "-" for ch in str(descriptor["version"] or "app"))[:80] or "app"
    return {
        "contract":BUNDLE_CONTRACT,
        "bundle":bundle,
        "descriptor":descriptor,
        "bundle_sha256":hashlib.sha256(bundle).hexdigest(),
        "file_name":f"{descriptor['app_key']}-{safe_version}.vp3app.zip",
    }


def inspect_bundle(bundle:bytes)->dict[str,Any]:
    if len(bundle)>MAX_BUNDLE_BYTES:
        raise AppDistributionError("Distribution bundle exceeds the size limit.",413)
    try:
        archive=zipfile.ZipFile(io.BytesIO(bundle),"r")
    except zipfile.BadZipFile as exc:
        raise AppDistributionError("Distribution bundle is not a valid ZIP archive.") from exc
    try:
        names=set(archive.namelist())
        if names!={"vp3-distribution.json","app-package.zip"}:
            raise AppDistributionError("Distribution bundle must contain only vp3-distribution.json and app-package.zip.")
        meta=json.loads(archive.read("vp3-distribution.json").decode("utf-8"))
        package=archive.read("app-package.zip")
    except (KeyError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise AppDistributionError("Distribution metadata is invalid.") from exc
    finally:
        archive.close()
    if not isinstance(meta,dict) or meta.get("contract")!=BUNDLE_CONTRACT:
        raise AppDistributionError("Distribution bundle contract is unsupported.")
    descriptor=meta.get("descriptor")
    if not isinstance(descriptor,dict) or descriptor.get("contract")!=CONTRACT:
        raise AppDistributionError("Distribution descriptor is invalid.")
    expected_hash=str(descriptor.get("package_sha256") or "")
    actual_hash=hashlib.sha256(package).hexdigest()
    if not expected_hash or not hmac.compare_digest(expected_hash,actual_hash):
        raise AppDistributionError("Distribution package checksum does not match the descriptor.",409)
    try:
        validation=homeserver_app_packages.validate_package(package,expected_app_key=str(descriptor.get("app_key") or ""))
    except homeserver_app_packages.AppPackageError as exc:
        raise AppDistributionError(str(exc),exc.status_code) from exc
    seal=dict(descriptor)
    signature=str(seal.pop("publisher_seal",""))
    local_publisher=str(descriptor.get("publisher_fingerprint") or "")==publisher_fingerprint()
    local_seal_valid=bool(local_publisher and signature and hmac.compare_digest(signature,_seal(seal)))
    return {
        "contract":BUNDLE_CONTRACT,
        "descriptor":descriptor,
        "package":package,
        "validation":validation,
        "bundle_sha256":hashlib.sha256(bundle).hexdigest(),
        "integrity_verified":True,
        "publisher_seal":{
            "issuer_fingerprint":str(descriptor.get("publisher_fingerprint") or ""),
            "locally_verifiable":local_publisher,
            "valid":local_seal_valid if local_publisher else None,
            "portable_trust":"cloud_private_share_grant",
        },
    }



def installed_provenance(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    meta=dict(app.get("metadata") or {})
    return {
        "contract":"vp3.app.distribution-provenance.v1",
        "app_key":app["app_key"],
        "installed_from_private_distribution":bool(meta.get("installed_from_private_distribution")),
        "publisher_fingerprint":str(meta.get("distribution_publisher_fingerprint") or ""),
        "distribution_id":str(meta.get("distribution_id") or ""),
        "package_sha256":str(meta.get("distribution_package_sha256") or ""),
        "installed_version":str(app.get("installed_version") or ""),
        "last_share_public_id":str(meta.get("distribution_share_public_id") or ""),
        "ownership_transferred":False,
    }


def preview_bundle(bundle:bytes,*,expected_package_sha256:str="")->dict[str,Any]:
    review=preview_bundle(bundle,expected_package_sha256=expected_package_sha256)
    inspected=inspect_bundle(bundle)
    descriptor=inspected["descriptor"]
    app_key=str(descriptor["app_key"])
    existing=None
    try:
        existing=homeserver_apps.get(app_key)
    except homeserver_apps.HomeServerAppError as exc:
        if exc.status_code!=404:
            raise AppDistributionError(str(exc),exc.status_code) from exc
    installed={}
    if existing:
        if existing["app_class"]!="user" or existing["protected_system_app"]:
            raise AppDistributionError("Distributed package cannot replace a protected VP3 system app.",409)
        installed=installed_provenance(app_key)
        prior_publisher=str(installed.get("publisher_fingerprint") or "")
        incoming_publisher=str(descriptor.get("publisher_fingerprint") or "")
        if prior_publisher and incoming_publisher and not hmac.compare_digest(prior_publisher,incoming_publisher):
            raise AppDistributionError("Shared app publisher fingerprint changed. Install is blocked.",409)
        if not prior_publisher and str(existing.get("installed_version") or ""):
            raise AppDistributionError("Existing local app is not trusted as this private distribution publisher. Archive or detach the local app before installing this shared build.",409)
    candidate_permissions=list(inspected["validation"]["manifest"].get("permissions") or [])
    permission_delta=(
        homeserver_app_security.permission_delta(app_key,candidate_permissions)
        if existing else {
            "added":[homeserver_app_security.permission_definition(p) for p in candidate_permissions],
            "removed":[],"retained":[],"requires_review":bool(candidate_permissions),
            "high_risk_added":[homeserver_app_security.permission_definition(p) for p in candidate_permissions if homeserver_app_security.permission_definition(p)["risk"]=="high"],
            "new_permissions_default_denied":True,
        }
    )
    current_schema=str(((existing or {}).get("metadata") or {}).get("data_schema_version") or "1")
    target_schema=str(inspected["validation"]["manifest"].get("data_schema_version") or "1")
    return {
        "contract":"vp3.app.distribution-update-review.v1",
        "descriptor":descriptor,
        "installed":installed or None,
        "new_install":existing is None,
        "update":bool(existing),
        "version_change":{
            "from":str(existing.get("installed_version") or "") if existing else "",
            "to":str(descriptor.get("version") or ""),
            "changed":bool(existing and str(existing.get("installed_version") or "")!=str(descriptor.get("version") or "")),
        },
        "package_change":{
            "from":str(installed.get("package_sha256") or ""),
            "to":str(descriptor.get("package_sha256") or ""),
            "changed":bool(existing and str(installed.get("package_sha256") or "")!=str(descriptor.get("package_sha256") or "")),
        },
        "publisher_continuity":True,
        "permission_delta":permission_delta,
        "schema_change":{"from":current_schema,"to":target_schema,"changed":current_schema!=target_schema},
        "requires_explicit_approval":True,
        "automatic_update":False,
    }


def _record_installed_provenance(app_key:str,descriptor:dict[str,Any],share_public_id:str="")->None:
    app=homeserver_apps.get(app_key)
    meta=dict(app.get("metadata") or {})
    meta.update({
        "installed_from_private_distribution":True,
        "distribution_contract":CONTRACT,
        "distribution_id":str(descriptor.get("distribution_id") or ""),
        "distribution_publisher_fingerprint":str(descriptor.get("publisher_fingerprint") or ""),
        "distribution_package_sha256":str(descriptor.get("package_sha256") or ""),
        "distribution_share_public_id":str(share_public_id or "")[:64],
        "distribution_version":str(descriptor.get("version") or ""),
    })
    with db() as connection:
        connection.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?",
            (json.dumps(meta,separators=(",",":"),sort_keys=True),app["app_id"]),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?,'app.private_distribution.installed','owner','local_owner',?)""",
            (app["app_id"],json.dumps({
                "distribution_id":str(descriptor.get("distribution_id") or ""),
                "publisher_fingerprint":str(descriptor.get("publisher_fingerprint") or ""),
                "package_sha256":str(descriptor.get("package_sha256") or ""),
                "version":str(descriptor.get("version") or ""),
                "share_public_id":str(share_public_id or "")[:64],
            },separators=(",",":"),sort_keys=True)),
        )

def install_bundle(bundle:bytes,*,approved:bool=False,expected_package_sha256:str="",share_public_id:str="")->dict[str,Any]:
    if approved is not True:
        raise AppDistributionError("Owner approval is required before installing a distributed app.",409)
    inspected=inspect_bundle(bundle)
    descriptor=inspected["descriptor"]
    if expected_package_sha256 and not hmac.compare_digest(
        str(expected_package_sha256).lower(),str(descriptor["package_sha256"]).lower()
    ):
        raise AppDistributionError("Private share grant does not match this app package.",409)
    app_key=str(descriptor["app_key"])
    try:
        app=homeserver_apps.get(app_key)
        if app["app_class"]!="user" or app["protected_system_app"]:
            raise AppDistributionError("Distributed package cannot replace a protected VP3 system app.",409)
    except homeserver_apps.HomeServerAppError as exc:
        if exc.status_code!=404:
            raise AppDistributionError(str(exc),exc.status_code) from exc
        manifest=inspected["validation"]["manifest"]
        homeserver_apps.register_user_app(
            app_key,
            str(manifest["name"]),
            source_type="zip",
            source_ref="vp3-private-distribution",
            metadata={
                "distribution_id":descriptor["distribution_id"],
                "publisher_fingerprint":descriptor["publisher_fingerprint"],
                "distribution_package_sha256":descriptor["package_sha256"],
                "distribution_contract":CONTRACT,
            },
        )
    release=homeserver_app_packages.install_package(
        app_key,
        inspected["package"],
        source_type="zip",
        source_provenance={
            "source_id":str(descriptor["distribution_id"]),
            "source_ref":"vp3-private-distribution",
            "source_revision":str(descriptor["package_sha256"]),
        },
    )
    _record_installed_provenance(app_key,descriptor,share_public_id)
    return {
        "contract":CONTRACT,
        "installed":True,
        "review":review,
        "descriptor":descriptor,
        "release":release,
        "integrity_verified":True,
        "share_hash_match":bool(expected_package_sha256),
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "export_bundle":True,
        "immutable_package_sha256":True,
        "publisher_local_seal":True,
        "private_share_portable_trust":"cloud_grant",
        "app_data_exported":False,
        "secrets_exported":False,
        "explicit_install_approval":True,
        "package_hash_binding":True,
        "ownership_transfer":False,
        "installed_share_provenance":True,
        "publisher_continuity_enforced":True,
        "update_review":True,
        "automatic_updates":False,
        "revocation_uninstalls_app":False,
        "marketplace":False,
    }

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))


def package(version:str,body:str)->bytes:
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1",
            "version":version,
            "runtime":"static",
            "entrypoint":"public/index.html",
        }))
        archive.writestr("public/index.html",body)
    return buffer.getvalue()


with tempfile.TemporaryDirectory(prefix="hosting-v160-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import hosting_cloud_control, hosting_cloud_deployment, pairing, remote_bridge

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    desired={
        "cloud_site_id":"cloud-site-deploy-0001",
        "revision":1,
        "display_name":"Deploy Reconcile",
        "requested_hostname":"deploy-sync.vp3.me",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":4_000_000,
        "sqlite_limit_bytes":1_000_000,
    }
    reconciled=remote_bridge.dispatch_remote_request("hosting.site.reconcile",desired,token)
    assert reconciled["ok"] is True
    assert reconciled["payload"]["blocked_reason"]=="deployment_required"

    first_package=package("1.6.0","release-one")
    first_sha=hashlib.sha256(first_package).hexdigest()
    begun=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":1,
        "package_sha256":first_sha,
        "package_bytes":len(first_package),
        "request_key":"cloud-deploy-1",
    },token)
    assert begun["ok"] is True
    transfer_id=begun["payload"]["transfer_id"]

    replay=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":1,
        "package_sha256":first_sha,
        "package_bytes":len(first_package),
        "request_key":"cloud-deploy-1",
    },token)
    assert replay["payload"]["transfer_id"]==transfer_id

    half=max(1,len(first_package)//2)
    chunks=[first_package[:half],first_package[half:]]
    part0=remote_bridge.dispatch_remote_request("hosting.deployment.chunk",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":transfer_id,
        "chunk_index":0,
        "data_b64":base64.b64encode(chunks[0]).decode(),
    },token)
    assert part0["ok"] is True
    assert part0["payload"]["next_chunk"]==1

    status=remote_bridge.dispatch_remote_request("hosting.deployment.status",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":transfer_id,
    },token)
    assert status["payload"]["received_bytes"]==len(chunks[0])
    assert status["payload"]["state"]=="receiving"

    duplicate=remote_bridge.dispatch_remote_request("hosting.deployment.chunk",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":transfer_id,
        "chunk_index":0,
        "data_b64":base64.b64encode(chunks[0]).decode(),
    },token)
    assert duplicate["ok"] is True
    assert duplicate["payload"]["next_chunk"]==1

    part1=remote_bridge.dispatch_remote_request("hosting.deployment.chunk",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":transfer_id,
        "chunk_index":1,
        "data_b64":base64.b64encode(chunks[1]).decode(),
    },token)
    assert part1["payload"]["received_bytes"]==len(first_package)

    committed=remote_bridge.dispatch_remote_request("hosting.deployment.commit",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":transfer_id,
    },token)
    assert committed["ok"] is True
    assert committed["payload"]["state"]=="applied"
    first_release=committed["payload"]["release_id"]
    assert first_release

    committed_again=remote_bridge.dispatch_remote_request("hosting.deployment.commit",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":transfer_id,
    },token)
    assert committed_again["payload"]["release_id"]==first_release

    ready=hosting_cloud_control.reconcile(dict(desired))
    assert ready["observed_state"]=="active"

    desired2=dict(desired)
    desired2["revision"]=2
    hosting_cloud_control.reconcile(desired2)

    stale=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":1,
        "package_sha256":first_sha,
        "package_bytes":len(first_package),
        "request_key":"stale",
    },token)
    assert stale["ok"] is False
    assert stale["status"]==409

    second_package=package("1.6.1","release-two")
    second_sha=hashlib.sha256(second_package).hexdigest()
    second=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":2,
        "package_sha256":second_sha,
        "package_bytes":len(second_package),
        "request_key":"cloud-deploy-2",
    },token)
    second_id=second["payload"]["transfer_id"]
    sent=remote_bridge.dispatch_remote_request("hosting.deployment.chunk",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":second_id,
        "chunk_index":0,
        "data_b64":base64.b64encode(second_package).decode(),
    },token)
    assert sent["ok"] is True
    second_commit=remote_bridge.dispatch_remote_request("hosting.deployment.commit",{
        "cloud_site_id":desired["cloud_site_id"],
        "transfer_id":second_id,
    },token)
    assert second_commit["ok"] is True
    second_release=second_commit["payload"]["release_id"]
    assert second_release!=first_release

    rolled=remote_bridge.dispatch_remote_request("hosting.deployment.rollback",{
        "cloud_site_id":desired["cloud_site_id"],
        "request_key":"cloud-rollback-1",
    },token)
    assert rolled["ok"] is True
    assert rolled["payload"]["state"]=="applied"
    assert rolled["payload"]["release_id"]==first_release
    assert rolled["payload"]["recovery"]["latest_verified"] is True
    assert rolled["payload"]["sqlite"]["healthy"] is True

    summary=remote_bridge.dispatch_remote_request("hosting.deployment.status",{
        "cloud_site_id":desired["cloud_site_id"],
    },token)
    assert summary["ok"] is True
    assert summary["payload"]["active_release_id"]==first_release
    assert summary["payload"]["recovery"]["recovery_points"]>=2
    assert "path" not in json.dumps(summary["payload"]).lower()

    cap=hosting_cloud_deployment.public_capability()
    assert cap["chunked_transfer"] is True
    assert cap["transfer_resume"] is True
    assert cap["homeserver_authoritative_execution"] is True
    assert cap["cloud_filesystem_access"] is False
    assert cap["cloud_raw_sql"] is False

print("HomeServer Hosting v1.60 Section 7: PASS")

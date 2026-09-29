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


with tempfile.TemporaryDirectory(prefix="hosting-v240-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import (
        hosting_cloud_control,
        hosting_deployment,
        hosting_health_recovery,
        hosting_operations,
        pairing,
        remote_bridge,
    )

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    desired={
        "cloud_site_id":"cloud-health-recovery-0001",
        "revision":1,
        "display_name":"Recovery Site",
        "requested_hostname":"",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":8_000_000,
        "sqlite_limit_bytes":1_000_000,
    }
    reconciled=remote_bridge.dispatch_remote_request("hosting.site.reconcile",desired,token)
    assert reconciled["ok"] is True

    def deploy(version:str,body:str,key:str)->str:
        payload=package(version,body)
        begun=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
            "cloud_site_id":desired["cloud_site_id"],
            "revision":1,
            "package_sha256":hashlib.sha256(payload).hexdigest(),
            "package_bytes":len(payload),
            "request_key":key,
        },token)
        assert begun["ok"] is True
        transfer_id=begun["payload"]["transfer_id"]
        sent=remote_bridge.dispatch_remote_request("hosting.deployment.chunk",{
            "cloud_site_id":desired["cloud_site_id"],
            "transfer_id":transfer_id,
            "chunk_index":0,
            "data_b64":base64.b64encode(payload).decode(),
        },token)
        assert sent["ok"] is True
        committed=remote_bridge.dispatch_remote_request("hosting.deployment.commit",{
            "cloud_site_id":desired["cloud_site_id"],
            "transfer_id":transfer_id,
        },token)
        assert committed["ok"] is True
        return str(committed["payload"]["release_id"])

    release1=deploy("2.4.1","known-good","health-v240-deploy-1")
    release2=deploy("2.4.2","broken-later","health-v240-deploy-2")
    assert release1!=release2

    local_site=str(hosting_cloud_control.status(desired["cloud_site_id"])["site_id"])
    policy=hosting_health_recovery.update_policy(local_site,{
        "interval_seconds":30,
        "failure_threshold":1,
        "max_recovery_attempts":2,
        "cooldown_seconds":30,
        "auto_reactivate":True,
        "auto_rollback":True,
        "auto_restore":False,
    })
    assert policy["failure_threshold"]==1
    assert policy["auto_rollback"] is True
    assert policy["auto_restore"] is False

    healthy=hosting_health_recovery.evaluate(local_site,execute_recovery=False)
    assert healthy["health_state"]=="healthy"

    active_root=hosting_deployment.active_public_root(local_site)
    (active_root/"index.html").unlink()

    recovered=hosting_health_recovery.evaluate(local_site,execute_recovery=True)
    assert recovered["health_state"]=="recovered"
    assert recovered["incident"] is not None
    assert recovered["incident"]["event_type"]=="hosting.health.incident.recovered"
    assert any(
        row["event_type"]=="hosting.health.recovery.action"
        and row["details"].get("action")=="rollback"
        and row["details"].get("status")=="succeeded"
        for row in recovered["history"]
    )

    deployment=hosting_deployment.deployment_status(local_site)
    assert deployment["active_release_id"]==release1
    assert deployment["previous_release_id"]==release2
    assert (hosting_deployment.active_public_root(local_site)/"index.html").read_text(encoding="utf-8")=="known-good"

    remote_status=remote_bridge.dispatch_remote_request("hosting.health.status",{
        "cloud_site_id":desired["cloud_site_id"],
    },token)
    assert remote_status["ok"] is True
    assert remote_status["payload"]["cloud_site_id"]==desired["cloud_site_id"]
    assert remote_status["payload"]["health_state"]=="recovered"
    assert remote_status["payload"]["homeserver_authoritative"] is True

    remote_check=remote_bridge.dispatch_remote_request("hosting.health.check",{
        "cloud_site_id":desired["cloud_site_id"],
        "execute_recovery":False,
    },token)
    assert remote_check["ok"] is True
    assert remote_check["payload"]["health_state"]=="healthy"

    updated=remote_bridge.dispatch_remote_request("hosting.health.policy.update",{
        "cloud_site_id":desired["cloud_site_id"],
        "policy":{"failure_threshold":2,"auto_restore":False},
    },token)
    assert updated["ok"] is True
    assert updated["payload"]["failure_threshold"]==2
    assert updated["payload"]["auto_restore"] is False

    dashboard=hosting_operations.dashboard()
    site_row=next(row for row in dashboard["sites"] if row["site_id"]==local_site)
    assert site_row["health_recovery"]["contract"]=="vp3.hosting.health-recovery.v1"
    assert site_row["health_recovery"]["health_state"]=="healthy"

    capability=hosting_health_recovery.public_capability()
    assert capability["periodic_health_checks"] is True
    assert capability["durable_incident_ledger"] is True
    assert capability["bounded_recovery_attempts"] is True
    assert capability["automatic_release_rollback"] is True
    assert capability["automatic_restore_default"] is False
    assert capability["homeserver_authoritative"] is True

print("HomeServer Hosting V2 Section 4 automated health recovery: PASS")

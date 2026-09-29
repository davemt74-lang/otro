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


with tempfile.TemporaryDirectory(prefix="hosting-v220-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import hosting_cloud_control, hosting_cloud_deployment, hosting_deployment, pairing, remote_bridge

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    desired={
        "cloud_site_id":"cloud-release-history-0001",
        "revision":1,
        "display_name":"Release History",
        "requested_hostname":"history.vp3.me",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":8_000_000,
        "sqlite_limit_bytes":1_000_000,
    }
    reconciled=remote_bridge.dispatch_remote_request("hosting.site.reconcile",desired,token)
    assert reconciled["ok"] is True

    def deploy(index:int)->str:
        payload=package(f"2.2.{index}",f"release-{index}")
        begun=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
            "cloud_site_id":desired["cloud_site_id"],
            "revision":1,
            "package_sha256":hashlib.sha256(payload).hexdigest(),
            "package_bytes":len(payload),
            "request_key":f"release-history-deploy-{index}",
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
        assert committed["payload"]["state"]=="applied"
        return str(committed["payload"]["release_id"])

    releases=[deploy(index) for index in range(1,5)]
    assert len(set(releases))==4

    catalog=remote_bridge.dispatch_remote_request("hosting.deployment.releases",{
        "cloud_site_id":desired["cloud_site_id"],
    },token)
    assert catalog["ok"] is True
    rows=catalog["payload"]["releases"]
    assert len(rows)==4
    assert catalog["payload"]["active_release_id"]==releases[3]
    assert catalog["payload"]["previous_release_id"]==releases[2]
    by_id={row["release_id"]:row for row in rows}
    assert by_id[releases[3]]["active"] is True
    assert by_id[releases[2]]["previous"] is True
    assert by_id[releases[0]]["app_version"]=="2.2.1"
    assert all("path" not in json.dumps(row).lower() for row in rows)

    promoted=remote_bridge.dispatch_remote_request("hosting.deployment.promote",{
        "cloud_site_id":desired["cloud_site_id"],
        "release_id":releases[0],
        "request_key":"promote-oldest",
    },token)
    assert promoted["ok"] is True
    assert promoted["payload"]["state"]=="applied"
    assert promoted["payload"]["release_id"]==releases[0]
    assert promoted["payload"]["previous_release_id"]==releases[3]
    assert promoted["payload"]["pre_promote_recovery_id"]
    assert promoted["payload"]["recovery"]["latest_verified"] is True
    assert promoted["payload"]["sqlite"]["healthy"] is True

    promote_replay=remote_bridge.dispatch_remote_request("hosting.deployment.promote",{
        "cloud_site_id":desired["cloud_site_id"],
        "release_id":releases[0],
        "request_key":"promote-oldest",
    },token)
    assert promote_replay["ok"] is True
    assert promote_replay["payload"]["release_id"]==releases[0]

    promote_conflict=remote_bridge.dispatch_remote_request("hosting.deployment.promote",{
        "cloud_site_id":desired["cloud_site_id"],
        "release_id":releases[1],
        "request_key":"promote-oldest",
    },token)
    assert promote_conflict["ok"] is False
    assert promote_conflict["status"]==409

    invalid=remote_bridge.dispatch_remote_request("hosting.deployment.promote",{
        "cloud_site_id":desired["cloud_site_id"],
        "release_id":"release_../../escape",
        "request_key":"invalid-release",
    },token)
    assert invalid["ok"] is False

    local_site=hosting_cloud_control.status(desired["cloud_site_id"])["site_id"]
    try:
        hosting_deployment.promote_release(local_site,"release_../../escape")
        raise AssertionError("Local release promotion accepted unsafe release id")
    except hosting_deployment.DeploymentError:
        pass

    after_promote=remote_bridge.dispatch_remote_request("hosting.deployment.releases",{
        "cloud_site_id":desired["cloud_site_id"],
    },token)
    after_by_id={row["release_id"]:row for row in after_promote["payload"]["releases"]}
    assert after_by_id[releases[0]]["active"] is True
    assert after_by_id[releases[3]]["previous"] is True

    pruned=remote_bridge.dispatch_remote_request("hosting.deployment.prune",{
        "cloud_site_id":desired["cloud_site_id"],
        "keep":2,
        "request_key":"prune-keep-two",
    },token)
    assert pruned["ok"] is True
    assert pruned["payload"]["state"]=="applied"
    assert releases[0] not in pruned["payload"]["deleted_release_ids"]
    assert releases[3] not in pruned["payload"]["deleted_release_ids"]
    assert releases[1] in pruned["payload"]["deleted_release_ids"]
    remaining={row["release_id"] for row in pruned["payload"]["releases"]}
    assert releases[0] in remaining
    assert releases[3] in remaining
    assert len(remaining)>=2
    assert "path" not in json.dumps(pruned["payload"]).lower()

    prune_replay=remote_bridge.dispatch_remote_request("hosting.deployment.prune",{
        "cloud_site_id":desired["cloud_site_id"],
        "keep":2,
        "request_key":"prune-keep-two",
    },token)
    assert prune_replay["ok"] is True
    assert prune_replay["payload"]["deleted_release_ids"]==pruned["payload"]["deleted_release_ids"]

    prune_conflict=remote_bridge.dispatch_remote_request("hosting.deployment.prune",{
        "cloud_site_id":desired["cloud_site_id"],
        "keep":3,
        "request_key":"prune-keep-two",
    },token)
    assert prune_conflict["ok"] is False
    assert prune_conflict["status"]==409

    summary=remote_bridge.dispatch_remote_request("hosting.deployment.status",{
        "cloud_site_id":desired["cloud_site_id"],
    },token)
    assert summary["ok"] is True
    assert summary["payload"]["active_release_id"]==releases[0]
    assert summary["payload"]["previous_release_id"]==releases[3]

    local_cap=hosting_deployment.public_capability()
    assert local_cap["release_history"] is True
    assert local_cap["historical_release_promotion"] is True
    assert local_cap["safe_release_retention"] is True
    assert local_cap["sqlite_schema_rewind_on_promotion"] is False

    cloud_cap=hosting_cloud_deployment.public_capability()
    assert cloud_cap["release_history"] is True
    assert cloud_cap["release_promotion"] is True
    assert cloud_cap["release_retention"] is True
    assert cloud_cap["promotion_acknowledgement"] is True
    assert cloud_cap["retention_acknowledgement"] is True

print("HomeServer Hosting V2 Section 2 release history: PASS")

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


with tempfile.TemporaryDirectory(prefix="hosting-v230-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import (
        hosting_cloud_control,
        hosting_diagnostics,
        hosting_operations,
        pairing,
        remote_bridge,
    )

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    desired={
        "cloud_site_id":"cloud-diagnostics-0001",
        "revision":1,
        "display_name":"Diagnostics Site",
        "requested_hostname":"diagnostics.vp3.me",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":8_000_000,
        "sqlite_limit_bytes":1_000_000,
    }
    reconciled=remote_bridge.dispatch_remote_request("hosting.site.reconcile",desired,token)
    assert reconciled["ok"] is True

    payload=package("2.3.0","diagnostics")
    begun=remote_bridge.dispatch_remote_request("hosting.deployment.begin",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":1,
        "package_sha256":hashlib.sha256(payload).hexdigest(),
        "package_bytes":len(payload),
        "request_key":"diagnostics-deploy-1",
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
    release_id=committed["payload"]["release_id"]

    # Reconcile again so active desired state can observe the newly deployed release.
    reconciled=remote_bridge.dispatch_remote_request("hosting.site.reconcile",desired,token)
    assert reconciled["ok"] is True
    local_site=str(hosting_cloud_control.status(desired["cloud_site_id"])["site_id"])

    hosting_diagnostics.observe_request(
        local_site,source="public",method="GET",
        path="/index.html?token=do-not-retain",status_code=200,duration_ms=120.5,response_bytes=1234,
    )
    hosting_diagnostics.observe_request(
        local_site,source="public",method="GET",
        path="/missing.html?password=hidden",status_code=404,duration_ms=31.2,
        error_class="http.error",
    )
    hosting_diagnostics.observe_request(
        local_site,source="public",method="POST",
        path="/checkout.php?authorization=secret",status_code=502,duration_ms=1450.0,
        error_class="php.runtime",
    )
    hosting_diagnostics.observe_request(
        local_site,source="health",method="GET",
        path="/",status_code=503,duration_ms=2050.0,error_class="health.failed",
    )

    summary=hosting_diagnostics.summary(local_site,window_minutes=60,recent_limit=20)
    assert summary["contract"]=="vp3.hosting.diagnostics.v1"
    assert summary["requests_total"]==4
    assert summary["client_error_total"]==1
    assert summary["server_error_total"]==2
    assert summary["php_failure_total"]==1
    assert summary["slow_request_total"]==2
    assert summary["average_duration_ms"]>0
    assert summary["p95_duration_ms"]>=1450
    assert summary["max_duration_ms"]>=2050
    assert summary["response_bytes_total"]==1234
    assert summary["last_deploy"]["release_id"]==release_id
    assert summary["runtime"]["state"]=="active"
    assert summary["runtime"]["serving_ready"] is True
    assert summary["sqlite"]["healthy"] is True
    assert summary["storage_bytes"]>0
    assert summary["sqlite_bytes"]>0
    assert "recent_5xx" in summary["issues"]
    assert "recent_php_failures" in summary["issues"]
    assert "recent_slow_requests" in summary["issues"]

    encoded=json.dumps(summary).lower()
    assert "do-not-retain" not in encoded
    assert "password=hidden" not in encoded
    assert "authorization=secret" not in encoded
    assert str(Path(data_dir)).lower() not in encoded
    assert all("?" not in str(item["path"]) for item in summary["recent"])

    remote=remote_bridge.dispatch_remote_request("hosting.diagnostics.summary",{
        "cloud_site_id":desired["cloud_site_id"],
        "window_minutes":60,
        "recent_limit":10,
    },token)
    assert remote["ok"] is True
    assert remote["payload"]["cloud_site_id"]==desired["cloud_site_id"]
    assert remote["payload"]["authoritative_source"]=="homeserver"
    assert remote["payload"]["requests_total"]==4

    dashboard=hosting_operations.dashboard()
    remote_site=next(item for item in dashboard["sites"] if item["site_id"]==local_site)
    assert remote_site["observability"]["requests_total"]==4
    assert remote_site["observability"]["recent"]==[]

    cap=hosting_diagnostics.public_capability()
    assert cap["request_counts"] is True
    assert cap["http_4xx_5xx"] is True
    assert cap["php_failures"] is True
    assert cap["query_strings_retained"] is False
    assert cap["request_bodies_retained"] is False
    assert cap["authorization_headers_retained"] is False
    assert cap["homeserver_authoritative"] is True

print("HomeServer Hosting V2 Section 3 diagnostics: PASS")

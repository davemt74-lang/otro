from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
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


with tempfile.TemporaryDirectory(prefix="hosting-v190-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import (
        hosting_cloud_control,
        hosting_deployment,
        hosting_entitlements,
        hosting_operations,
        hosting_public,
        pairing,
        remote_bridge,
    )

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    ent=hosting_entitlements.reconcile({
        "revision":1,
        "package_key":"team-builder",
        "max_sites":3,
        "max_active_sites":3,
        "max_public_routes":3,
        "max_storage_bytes_per_site":4_000_000,
        "max_sqlite_bytes_per_site":1_000_000,
        "allowed_runtimes":["static","php"],
    })
    assert ent["within_entitlement"] is True

    desired={
        "cloud_site_id":"cloud-release-000001",
        "revision":1,
        "display_name":"Release Hardened Site",
        "requested_hostname":"release.vp3.me",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":2_000_000,
        "sqlite_limit_bytes":500_000,
    }
    first=hosting_cloud_control.reconcile(desired)
    site_id=first["site_id"]

    release1=hosting_deployment.deploy_package(site_id,package("1.9.0","one"),request_key="release-hardening-1")
    release2=hosting_deployment.deploy_package(site_id,package("1.9.1","two"),request_key="release-hardening-2")
    assert release1["release_id"]!=release2["release_id"]

    ready=hosting_cloud_control.reconcile(dict(desired))
    assert ready["observed_state"]=="active"

    future=(datetime.now(timezone.utc)+timedelta(days=45)).isoformat()
    route=hosting_public.reconcile(
        desired["cloud_site_id"],
        revision=1,
        hostname="release.vp3.me",
        desired_state="active",
        hostname_verified=True,
        tls_state="active",
        certificate_not_after=future,
    )
    assert route["route_ready"] is True

    dashboard=hosting_operations.dashboard()
    assert dashboard["healthy"] is True
    assert dashboard["counts"]["sites"]==1
    assert dashboard["counts"]["active"]==1
    assert dashboard["counts"]["public_routes"]==1
    serialized=json.dumps(dashboard).lower()
    assert "route_token" not in serialized
    assert "site.sqlite" not in serialized
    assert "database_relpath" not in serialized

    recovery=hosting_operations.execute(site_id,"recovery.create","release-op-recovery-0001")
    assert recovery["replayed"] is False
    assert recovery["recovery_id"].startswith("recovery_")
    recovery_replay=hosting_operations.execute(site_id,"recovery.create","release-op-recovery-0001")
    assert recovery_replay["replayed"] is True
    assert recovery_replay["recovery_id"]==recovery["recovery_id"]

    try:
        hosting_operations.execute(site_id,"site.suspend","release-op-recovery-0001")
        raise AssertionError("idempotency key was reused across actions")
    except hosting_operations.HostingOperationsError as exc:
        assert exc.status_code==409

    try:
        hosting_operations.execute(site_id,"site.suspend","release-op-suspend-unconfirmed")
        raise AssertionError("consequential action did not require confirmation")
    except hosting_operations.HostingOperationsError as exc:
        assert exc.status_code==409

    suspended=hosting_operations.execute(site_id,"site.suspend","release-op-suspend-0001",confirmed=True)
    assert suspended["state"]=="suspended"
    activated=hosting_operations.execute(site_id,"site.activate","release-op-activate-0001",confirmed=True)
    assert activated["state"]=="active"

    rolled=hosting_operations.execute(site_id,"deployment.rollback","release-op-rollback-0001",confirmed=True)
    assert rolled["release_id"]==release1["release_id"]
    rolled_replay=hosting_operations.execute(site_id,"deployment.rollback","release-op-rollback-0001",confirmed=True)
    assert rolled_replay["replayed"] is True
    assert rolled_replay["release_id"]==release1["release_id"]

    remote_dash=remote_bridge.dispatch_remote_request("hosting.dashboard",{},token)
    assert remote_dash["ok"] is True
    assert remote_dash["payload"]["counts"]["sites"]==1

    remote_recovery=remote_bridge.dispatch_remote_request("hosting.operation.execute",{
        "site_id":site_id,
        "action":"recovery.create",
        "idempotency_key":"release-op-remote-0001",
    },token)
    assert remote_recovery["ok"] is True
    assert remote_recovery["payload"]["recovery_id"].startswith("recovery_")

    bad_action=remote_bridge.dispatch_remote_request("hosting.operation.execute",{
        "site_id":site_id,
        "action":"site.delete",
        "idempotency_key":"release-op-delete-0001",
        "confirmed":True,
    },token)
    assert bad_action["ok"] is False
    assert bad_action["status"]==403

    cap=hosting_operations.public_capability()
    assert cap["read_only_dashboard"] is True
    assert cap["uses_canonical_services"] is True
    assert cap["raw_filesystem_access"] is False
    assert cap["raw_sql_access"] is False
    assert cap["billing_mutations"] is False
    assert cap["explicit_confirmation_for_consequential_actions"] is True
    assert cap["cloud_desired_state_activation_gate"] is True
    assert cap["automatic_destructive_actions"] is False

print("HomeServer Hosting v1.90 Section 10 release hardening: PASS")

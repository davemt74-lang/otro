from __future__ import annotations

import io,json,os,sys,tempfile,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def pkg(body:str)->bytes:
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("vp3-hosting.json",json.dumps({"contract":"vp3.hosting.package.v1","version":"1.8.0","runtime":"static","entrypoint":"public/index.html"}))
        z.writestr("public/index.html",body)
    return b.getvalue()

with tempfile.TemporaryDirectory(prefix="hosting-v180-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import initialize_database
    from app.services import hosting_cloud_control,hosting_deployment,hosting_entitlements,hosting_public,pairing,remote_bridge

    initialize_database()
    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    ent={
      "revision":1,"package_key":"basic","max_sites":1,"max_active_sites":1,"max_public_routes":1,
      "max_storage_bytes_per_site":2_000_000,"max_sqlite_bytes_per_site":500_000,"allowed_runtimes":["static"],
    }
    r=remote_bridge.dispatch_remote_request("hosting.entitlements.reconcile",ent,token)
    assert r["ok"] is True and r["payload"]["reconcile_result"]=="applied"

    desired={"cloud_site_id":"cloud-ent-00000001","revision":1,"display_name":"Entitled","requested_hostname":"ent.vp3.me",
      "runtime_kind":"static","desired_state":"active","storage_limit_bytes":1_000_000,"sqlite_limit_bytes":250_000}
    first=hosting_cloud_control.reconcile(desired)
    sid=first["site_id"]

    from app.services import hosting_runtime
    try:
        hosting_runtime.create_site(
            "Local Bypass",
            requested_hostname="local-bypass.vp3.me",
            runtime_kind="static",
            storage_limit_bytes=1_000_000,
            sqlite_limit_bytes=250_000,
        )
        raise AssertionError("canonical create_site bypassed package limit")
    except hosting_entitlements.EntitlementError:
        pass
    hosting_deployment.deploy_package(sid,pkg("ok"),request_key="ent-deploy-1")
    ready=hosting_cloud_control.reconcile(dict(desired))
    assert ready["observed_state"]=="active"

    try:
        hosting_cloud_control.reconcile({**desired,"cloud_site_id":"cloud-ent-00000002","requested_hostname":"two.vp3.me"})
        raise AssertionError("site limit bypassed")
    except hosting_entitlements.EntitlementError as exc:
        assert exc.status_code==403

    try:
        hosting_cloud_control.reconcile({**desired,"cloud_site_id":"cloud-ent-00000003","requested_hostname":"php.vp3.me","runtime_kind":"php"})
        raise AssertionError("runtime entitlement bypassed")
    except hosting_entitlements.EntitlementError:
        pass

    future=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()
    route=hosting_public.reconcile(desired["cloud_site_id"],revision=1,hostname="ent.vp3.me",desired_state="active",
      hostname_verified=True,tls_state="active",certificate_not_after=future)
    assert route["route_ready"] is True

    downgraded={**ent,"revision":2,"max_sites":0,"max_active_sites":0,"max_public_routes":0}
    d=hosting_entitlements.reconcile(downgraded)
    assert d["within_entitlement"] is False
    assert set(d["overages"])=={"sites","active_sites","public_routes"}
    assert hosting_cloud_control.status(desired["cloud_site_id"])["site_id"]==sid
    assert (hosting_deployment.active_public_root(sid)/"index.html").read_text()=="ok"

    try:
        hosting_public.reconcile(desired["cloud_site_id"],revision=1,hostname="ent.vp3.me",desired_state="inactive",
          hostname_verified=True,tls_state="active",certificate_not_after=future)
    except Exception:
        pass

    status=remote_bridge.dispatch_remote_request("hosting.entitlements.status",{},token)
    assert status["ok"] is True
    assert status["payload"]["package_key"]=="basic"
    cap=hosting_entitlements.public_capability()
    assert cap["cloud_authoritative_package"] is True
    assert cap["downgrade_deletes_data"] is False
    assert cap["billing_engine"] is False

print("HomeServer Hosting v1.80 Section 9: PASS")

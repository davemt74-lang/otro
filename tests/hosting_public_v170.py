from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))


def package(body:str)->bytes:
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1",
            "version":"1.7.0",
            "runtime":"static",
            "entrypoint":"public/index.html",
        }))
        archive.writestr("public/index.html",body)
    return buffer.getvalue()


def request_for(path:str,query:str="")->Request:
    async def receive():
        return {"type":"http.request","body":b"","more_body":False}
    return Request({
        "type":"http",
        "http_version":"1.1",
        "method":"GET",
        "scheme":"http",
        "path":path,
        "raw_path":path.encode(),
        "query_string":query.encode(),
        "headers":[],
        "client":("127.0.0.1",12345),
        "server":("127.0.0.1",4377),
    },receive)


with tempfile.TemporaryDirectory(prefix="hosting-v170-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import initialize_database
    from app.hosting_public_api import public_ingress
    from app.services import hosting_cloud_control, hosting_deployment, hosting_public, pairing, remote_bridge

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    desired={
        "cloud_site_id":"cloud-site-route-0001",
        "revision":1,
        "display_name":"Public Route",
        "requested_hostname":"public.vp3.me",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":4_000_000,
        "sqlite_limit_bytes":1_000_000,
    }
    first=hosting_cloud_control.reconcile(desired)
    site_id=first["site_id"]
    hosting_deployment.deploy_package(site_id,package("public-ready"),request_key="route-release-1")
    ready=hosting_cloud_control.reconcile(dict(desired))
    assert ready["observed_state"]=="active"

    future=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()
    activated=remote_bridge.dispatch_remote_request("hosting.route.reconcile",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":1,
        "hostname":"public.vp3.me",
        "desired_state":"active",
        "hostname_verified":True,
        "tls_state":"active",
        "certificate_not_after":future,
    },token)
    assert activated["ok"] is True
    route=activated["payload"]
    route_token=route["route_token"]
    assert route["route_ready"] is True
    assert route["home_server_public_listener"] is False

    status=remote_bridge.dispatch_remote_request("hosting.route.status",{
        "cloud_site_id":desired["cloud_site_id"],
    },token)
    assert status["ok"] is True
    assert status["payload"]["route_ready"] is True
    assert "route_token" not in status["payload"]

    cloud_status=hosting_cloud_control.status(desired["cloud_site_id"])
    assert cloud_status["public_routing"] is True
    assert cloud_status["public_route"]["route_ready"] is True
    assert "route_token" not in json.dumps(cloud_status)

    auth=hosting_public.authorize_ingress("public.vp3.me",route_token,"https")
    assert auth["site_id"]==site_id
    try:
        hosting_public.authorize_ingress("public.vp3.me","wrong-token","https")
        raise AssertionError("wrong route token was accepted")
    except hosting_public.PublicRoutingError as exc:
        assert exc.status_code==403

    try:
        hosting_public.authorize_ingress("public.vp3.me",route_token,"http")
        raise AssertionError("plain HTTP ingress was accepted")
    except hosting_public.PublicRoutingError as exc:
        assert exc.status_code==426

    response=asyncio.run(public_ingress(
        "index.html",
        request_for("/__vp3/hosting/index.html"),
        route_token=route_token,
        forwarded_host="public.vp3.me",
        forwarded_proto="https",
    ))
    assert str(getattr(response,"path","")).endswith("index.html")

    redirect=asyncio.run(public_ingress(
        "index.html",
        request_for("/__vp3/hosting/index.html","a=1"),
        route_token=route_token,
        forwarded_host="public.vp3.me",
        forwarded_proto="http",
    ))
    assert redirect.status_code==308
    assert redirect.headers["location"]=="https://public.vp3.me/index.html?a=1"

    expired=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    desired2=dict(desired)
    desired2["revision"]=2
    hosting_cloud_control.reconcile(desired2)
    bad_tls=remote_bridge.dispatch_remote_request("hosting.route.reconcile",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":2,
        "hostname":"public.vp3.me",
        "desired_state":"active",
        "hostname_verified":True,
        "tls_state":"active",
        "certificate_not_after":expired,
    },token)
    assert bad_tls["ok"] is False
    assert bad_tls["status"]==409

    renewed=remote_bridge.dispatch_remote_request("hosting.route.reconcile",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":2,
        "hostname":"public.vp3.me",
        "desired_state":"active",
        "hostname_verified":True,
        "tls_state":"renewing",
        "certificate_not_after":future,
    },token)
    assert renewed["ok"] is True
    assert renewed["payload"]["route_ready"] is True

    other={
        "cloud_site_id":"cloud-site-route-0002",
        "revision":1,
        "display_name":"Collision",
        "requested_hostname":"public.vp3.me",
        "runtime_kind":"static",
        "desired_state":"configured",
        "storage_limit_bytes":4_000_000,
        "sqlite_limit_bytes":1_000_000,
    }
    hosting_cloud_control.reconcile(other)
    collision=remote_bridge.dispatch_remote_request("hosting.route.reconcile",{
        "cloud_site_id":other["cloud_site_id"],
        "revision":1,
        "hostname":"public.vp3.me",
        "desired_state":"active",
        "hostname_verified":True,
        "tls_state":"active",
        "certificate_not_after":future,
    },token)
    assert collision["ok"] is False
    assert collision["status"]==409

    desired3=dict(desired)
    desired3.update({"revision":3,"desired_state":"suspended"})
    hosting_cloud_control.reconcile(desired3)
    inactive=remote_bridge.dispatch_remote_request("hosting.route.reconcile",{
        "cloud_site_id":desired["cloud_site_id"],
        "revision":3,
        "hostname":"public.vp3.me",
        "desired_state":"inactive",
        "hostname_verified":True,
        "tls_state":"active",
        "certificate_not_after":future,
    },token)
    assert inactive["ok"] is True
    assert inactive["payload"]["route_ready"] is False
    try:
        hosting_public.authorize_ingress("public.vp3.me",route_token,"https")
        raise AssertionError("inactive route accepted ingress")
    except hosting_public.PublicRoutingError as exc:
        assert exc.status_code==503

    cap=hosting_public.public_capability()
    assert cap["cloud_authoritative_hostname"] is True
    assert cap["cloud_edge_tls"] is True
    assert cap["route_token_binding"] is True
    assert cap["home_server_public_listener"] is False
    assert cap["raw_certificate_private_key_on_homeserver"] is False

print("HomeServer Hosting v1.70 Section 8: PASS")

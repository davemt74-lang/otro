from __future__ import annotations

import asyncio
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


def package(body:str)->bytes:
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1",
            "version":"2.1.0",
            "runtime":"static",
            "entrypoint":"public/index.html",
        }))
        archive.writestr("public/index.html",body)
    return buffer.getvalue()


async def consume(response):
    scope={
        "type":"http","http_version":"1.1","method":"GET","scheme":"http",
        "path":"/","raw_path":b"/","query_string":b"",
        "headers":[],"client":("127.0.0.1",1),"server":("127.0.0.1",4377),
    }
    async def receive():
        return {"type":"http.request","body":b"","more_body":False}
    async def send(message):
        return None
    await response(scope,receive,send)


with tempfile.TemporaryDirectory(prefix="hosting-v210-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import hosting_deployment,hosting_observability,hosting_operations,hosting_runtime,hosting_serving,pairing,remote_bridge

    initialize_database()
    site=hosting_runtime.create_site("Observed Site",runtime_kind="static")
    site_id=site["site_id"]
    hosting_deployment.deploy_package(site_id,package("hello"),request_key="v210-observe")

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    response=hosting_serving.serve(
        site_id,
        "index.html",
        method="GET",
        query_string="token=super-secret&email=user@example.com",
        request_headers={"Authorization":"Bearer secret","Cookie":"session=secret"},
    )
    asyncio.run(consume(response))

    try:
        hosting_serving.serve(site_id,"reset/ABCDEF0123456789ABCDEF0123456789",method="GET",query_string="secret=never-log")
        raise AssertionError("missing resource unexpectedly served")
    except hosting_serving.ServingError as exc:
        assert exc.status_code==404

    summary=hosting_observability.summary(site_id)
    assert summary["requests_total"]==2
    assert summary["success_total"]==1
    assert summary["client_error_total"]==1
    assert summary["server_error_total"]==0
    assert summary["bytes_out_total"]==5
    assert summary["average_duration_ms"]>=0
    assert summary["status_counts"]["200"]==1
    assert summary["status_counts"]["404"]==1

    recent=hosting_observability.recent(site_id,limit=10)
    assert len(recent["items"])==2
    serialized=json.dumps(recent).lower()
    assert "super-secret" not in serialized
    assert "user@example.com" not in serialized
    assert "bearer secret" not in serialized
    assert "session=secret" not in serialized
    assert "secret=never-log" not in serialized
    assert "abcdef0123456789abcdef0123456789" not in serialized
    assert "[redacted]" in serialized
    assert "authorization" not in serialized
    assert "cookie" not in serialized
    assert "query" not in serialized
    assert "body" not in serialized
    assert "sqlite" not in serialized
    assert str(Path(data_dir)).lower() not in serialized

    remote_status=remote_bridge.dispatch_remote_request("hosting.observability.status",{"site_id":site_id},token)
    assert remote_status["ok"] is True
    assert remote_status["payload"]["requests_total"]==2
    remote_recent=remote_bridge.dispatch_remote_request("hosting.observability.recent",{"site_id":site_id,"limit":10},token)
    assert remote_recent["ok"] is True
    assert len(remote_recent["payload"]["items"])==2
    assert "super-secret" not in json.dumps(remote_recent).lower()

    dashboard=hosting_operations.dashboard()
    observed=next(item for item in dashboard["sites"] if item["site_id"]==site_id)
    assert observed["observability"]["requests_total"]==2
    assert dashboard["counts"]["requests_total"]==2

    original_max=hosting_observability.MAX_LOG_BYTES
    hosting_observability.MAX_LOG_BYTES=1600
    try:
        for i in range(60):
            hosting_observability.record(
                site_id,
                method="GET",
                request_path=f"/asset-{i}.js?ignore=1",
                status_code=200,
                duration_ms=1.25,
                bytes_out=10,
                runtime_kind="static",
                release_id="release_test",
            )
        log_path=hosting_observability._log_path(site_id)
        assert log_path.stat().st_size<=hosting_observability.MAX_LOG_BYTES
        bounded=hosting_observability.recent(site_id,limit=200)
        assert len(bounded["items"])<=hosting_observability.MAX_RECENT_EVENTS
    finally:
        hosting_observability.MAX_LOG_BYTES=original_max

    cap=hosting_observability.public_capability()
    assert cap["aggregate_metrics"] is True
    assert cap["bounded_recent_request_log"] is True
    assert cap["query_strings_logged"] is False
    assert cap["request_bodies_logged"] is False
    assert cap["headers_logged"] is False
    assert cap["cookies_logged"] is False
    assert cap["tokens_logged"] is False
    assert cap["filesystem_paths_logged"] is False
    assert cap["raw_sql_logged"] is False

print("HomeServer Hosting V2 Section 2 observability: PASS")

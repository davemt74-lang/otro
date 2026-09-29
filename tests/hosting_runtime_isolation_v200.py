from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import threading
import time
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


async def consume_response(response):
    scope={
        "type":"http",
        "http_version":"1.1",
        "method":"GET",
        "scheme":"http",
        "path":"/",
        "raw_path":b"/",
        "query_string":b"",
        "headers":[],
        "client":("127.0.0.1",1234),
        "server":("127.0.0.1",4377),
    }
    sent=[]
    async def receive():
        return {"type":"http.request","body":b"","more_body":False}
    async def send(message):
        sent.append(message)
    await response(scope,receive,send)
    return sent


with tempfile.TemporaryDirectory(prefix="hosting-v200-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import hosting_deployment, hosting_runtime, hosting_scheduler, hosting_serving

    initialize_database()
    hosting_scheduler.reset_for_tests()

    site1=hosting_runtime.create_site("Scheduler One",runtime_kind="static")
    site2=hosting_runtime.create_site("Scheduler Two",runtime_kind="static")
    s1=site1["site_id"]
    s2=site2["site_id"]
    hosting_deployment.deploy_package(s1,package("2.0.0","one"),request_key="v200-one")
    hosting_deployment.deploy_package(s2,package("2.0.0","two"),request_key="v200-two")

    # Static response retains a slot until the response body has completed.
    response=hosting_serving.serve(s1,"index.html")
    assert hosting_scheduler.status(s1)["inflight"]==1
    asyncio.run(consume_response(response))
    assert hosting_scheduler.status(s1)["inflight"]==0
    assert hosting_scheduler.status(s1)["completed"]>=1

    # Tighten limits for a deterministic scheduler contract test.
    original_site=hosting_scheduler.SITE_MAX_INFLIGHT
    original_global=hosting_scheduler.GLOBAL_MAX_INFLIGHT
    hosting_scheduler.SITE_MAX_INFLIGHT=2
    hosting_scheduler.GLOBAL_MAX_INFLIGHT=3
    try:
        hosting_scheduler.acquire(s1,timeout=0)
        hosting_scheduler.acquire(s1,timeout=0)
        try:
            hosting_scheduler.acquire(s1,timeout=0)
            raise AssertionError("per-site concurrency limit was bypassed")
        except hosting_scheduler.SchedulerError as exc:
            assert exc.status_code==503

        # A saturated site does not consume another site's reserved eligibility.
        hosting_scheduler.acquire(s2,timeout=0)
        assert hosting_scheduler.status(s2)["inflight"]==1

        # Global ceiling still bounds total HomeServer runtime pressure.
        try:
            hosting_scheduler.acquire(s2,timeout=0)
            raise AssertionError("global concurrency limit was bypassed")
        except hosting_scheduler.SchedulerError as exc:
            assert exc.status_code==503

        hosting_scheduler.release(s1)
        hosting_scheduler.release(s1)
        hosting_scheduler.release(s2)
    finally:
        hosting_scheduler.SITE_MAX_INFLIGHT=original_site
        hosting_scheduler.GLOBAL_MAX_INFLIGHT=original_global

    # Drain rejects new requests and waits for active work to finish.
    hosting_scheduler.acquire(s1,timeout=0)
    drain_result={}
    def drain():
        drain_result["value"]=hosting_scheduler.begin_drain(s1,timeout=2.0)
    t=threading.Thread(target=drain)
    t.start()
    deadline=time.time()+1
    while time.time()<deadline and not hosting_scheduler.status(s1)["draining"]:
        time.sleep(0.01)
    assert hosting_scheduler.status(s1)["draining"] is True
    try:
        hosting_scheduler.acquire(s1,timeout=0)
        raise AssertionError("draining site admitted a new request")
    except hosting_scheduler.SchedulerError as exc:
        assert exc.status_code==503
    hosting_scheduler.release(s1)
    t.join(timeout=2)
    assert not t.is_alive()
    assert drain_result["value"]["inflight"]==0
    hosting_scheduler.end_drain(s1)
    assert hosting_scheduler.status(s1)["draining"] is False

    # Release activation must wait for in-flight traffic before switching.
    hosting_scheduler.acquire(s1,timeout=0)
    deploy_result={}
    deploy_error={}
    def deploy_next():
        try:
            deploy_result["value"]=hosting_deployment.deploy_package(
                s1,package("2.0.1","next"),request_key="v200-next"
            )
        except Exception as exc:
            deploy_error["value"]=exc
    worker=threading.Thread(target=deploy_next)
    worker.start()
    deadline=time.time()+3
    while time.time()<deadline and not hosting_scheduler.status(s1)["draining"]:
        time.sleep(0.01)
    assert hosting_scheduler.status(s1)["draining"] is True
    before=hosting_deployment.deployment_status(s1)["active_release"]["app_version"]
    assert before=="2.0.0"
    hosting_scheduler.release(s1)
    worker.join(timeout=4)
    assert not worker.is_alive()
    assert "value" not in deploy_error
    assert deploy_result["value"]["app_version"]=="2.0.1"
    after=hosting_deployment.deployment_status(s1)["active_release"]["app_version"]
    assert after=="2.0.1"
    assert hosting_scheduler.status(s1)["draining"] is False

    # Crash/error accounting never leaks a slot.
    try:
        with hosting_scheduler.request_slot(s2,timeout=0):
            raise RuntimeError("simulated request crash")
    except RuntimeError:
        pass
    state=hosting_scheduler.status(s2)
    assert state["inflight"]==0
    assert state["failed"]>=1

    cap=hosting_scheduler.public_capability()
    assert cap["per_site_isolation"] is True
    assert cap["global_admission_control"] is True
    assert cap["bounded_backpressure"] is True
    assert cap["graceful_drain"] is True
    assert cap["crash_accounting"] is True
    assert cap["container_runtime"] is False

print("HomeServer Hosting V2 Section 1 runtime isolation: PASS")

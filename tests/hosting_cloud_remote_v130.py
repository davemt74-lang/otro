from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="hosting-v130-remote-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import pairing, remote_bridge

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["hosting.manage"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    vp3_token=vp3["claim_token"]

    other=pairing.create_pairing_request("other-cloud","Other Cloud",["hosting.manage"])
    assert pairing.approve_pairing(other["code"]) is not None
    other_token=other["claim_token"]

    desired={
        "cloud_site_id":"cloud-site-remote-0001",
        "revision":1,
        "display_name":"Remote Managed Site",
        "requested_hostname":"remote.vp3.me",
        "runtime_kind":"static",
        "desired_state":"configured",
        "storage_limit_bytes":1000000,
        "sqlite_limit_bytes":250000,
    }

    created=remote_bridge.dispatch_remote_request("hosting.site.reconcile",desired,vp3_token)
    assert created["ok"] is True
    assert created["payload"]["cloud_site_id"]=="cloud-site-remote-0001"

    status=remote_bridge.dispatch_remote_request(
        "hosting.site.status",
        {"cloud_site_id":"cloud-site-remote-0001"},
        vp3_token,
    )
    assert status["ok"] is True
    assert status["payload"]["requested_hostname"]=="remote.vp3.me"

    inventory=remote_bridge.dispatch_remote_request("hosting.inventory",{},vp3_token)
    assert inventory["ok"] is True
    assert inventory["payload"]["count"]==1
    assert inventory["payload"]["cloud_authoritative_identity"] is True
    assert inventory["payload"]["homeserver_authoritative_runtime"] is True
    assert inventory["payload"]["public_routing"] is False

    try:
        remote_bridge.dispatch_remote_request("hosting.inventory",{},other_token)
        raise AssertionError("non-VP3 app was allowed to control hosting")
    except remote_bridge.RemoteBridgeError as exc:
        assert "restricted to the paired VP3 Cloud app" in str(exc)

    no_permission=pairing.create_pairing_request("vp3-no-hosting","VP3 No Hosting",["agent.chat"])
    assert pairing.approve_pairing(no_permission["code"]) is not None
    try:
        remote_bridge.dispatch_remote_request("hosting.inventory",{},no_permission["claim_token"])
        raise AssertionError("missing hosting.manage permission was accepted")
    except remote_bridge.RemoteBridgeError:
        pass

print("HomeServer Hosting v1.30 remote control: PASS")

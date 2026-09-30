from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v190-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database  # noqa: E402
    from app.services import homeserver_apps, pairing, remote_bridge  # noqa: E402

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["agent.chat"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    catalog=remote_bridge.dispatch_remote_request("apps.system.catalog",{},token)
    assert catalog["ok"] is True
    assert catalog["payload"]["contract"]=="vp3.app.prebuilt-catalog.v1"
    assert {item["key"] for item in catalog["payload"]["packages"]}=={"vp3.notes","vp3.inventory","vp3.checklists"}

    before=remote_bridge.dispatch_remote_request("apps.system.status",{"app_key":"vp3.notes"},token)
    assert before["ok"] is True
    assert before["payload"]["installed"] is False
    assert before["payload"]["state"]=="available"

    installed=remote_bridge.dispatch_remote_request("apps.system.install",{"app_key":"vp3.notes"},token)
    assert installed["ok"] is True
    assert installed["payload"]["changed"] is True
    assert installed["payload"]["installed"] is True
    assert installed["payload"]["current"] is True
    assert installed["payload"]["state"]=="running"
    assert homeserver_apps.get("vp3.notes")["protected_system_app"] is True

    replay=remote_bridge.dispatch_remote_request("apps.system.install",{"app_key":"vp3.notes"},token)
    assert replay["ok"] is True
    assert replay["payload"]["changed"] is False
    assert replay["payload"]["reason"]=="already_current"

    deactivated=remote_bridge.dispatch_remote_request("apps.system.deactivate",{"app_key":"vp3.notes"},token)
    assert deactivated["ok"] is True
    assert deactivated["payload"]["changed"] is True
    assert deactivated["payload"]["installed"] is True
    assert deactivated["payload"]["state"]=="stopped"
    assert homeserver_apps.get("vp3.notes")["lifecycle_state"]=="stopped"

    deactivate_replay=remote_bridge.dispatch_remote_request("apps.system.deactivate",{"app_key":"vp3.notes"},token)
    assert deactivate_replay["ok"] is True
    assert deactivate_replay["payload"]["changed"] is False
    assert deactivate_replay["payload"]["reason"]=="already_inactive"

    restored=remote_bridge.dispatch_remote_request("apps.system.install",{"app_key":"vp3.notes"},token)
    assert restored["ok"] is True
    assert restored["payload"]["installed"] is True
    assert restored["payload"]["state"]=="running"

    reconcile=remote_bridge.dispatch_remote_request(
        "apps.system.reconcile",
        {"app_keys":["vp3.notes","vp3.inventory","missing.app"]},
        token,
    )
    assert reconcile["ok"] is True
    payload=reconcile["payload"]
    assert payload["contract"]=="vp3.system-app-reconciliation.v1"
    assert payload["runtime_authority"]=="homeserver"
    assert len(payload["items"])==3
    assert payload["items"][0]["installed"] is True
    assert payload["items"][1]["installed"] is False
    assert payload["items"][2]["status"]==404

    missing=remote_bridge.dispatch_remote_request("apps.system.install",{"app_key":"missing.app"},token)
    assert missing["ok"] is False
    assert missing["status"]==404

    other=pairing.create_pairing_request("other-cloud","Other Cloud",["agent.chat"])
    assert pairing.approve_pairing(other["code"]) is not None
    try:
        remote_bridge.dispatch_remote_request("apps.system.catalog",{},other["claim_token"])
        raise AssertionError("non-VP3 paired app was allowed to inspect System Apps")
    except remote_bridge.RemoteBridgeError as exc:
        assert "restricted to the paired VP3 Cloud app" in str(exc)

    try:
        remote_bridge.dispatch_remote_request("apps.system.reconcile",{"app_keys":["vp3.notes"]*101},token)
        raise AssertionError("oversized reconciliation request was accepted")
    except remote_bridge.RemoteBridgeError:
        pass

    relay=(ROOT/"relay"/"app.py").read_text(encoding="utf-8")
    for operation in ("apps.system.catalog","apps.system.status","apps.system.install","apps.system.deactivate","apps.system.reconcile"):
        assert operation in relay

print("HomeServer Apps V1 Section 10 Cloud ownership install bridge: PASS")

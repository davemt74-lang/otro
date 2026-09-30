from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-system-permissions-v220-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import (
        homeserver_app_packages,
        homeserver_app_prebuilt,
        homeserver_app_security,
        homeserver_apps,
        pairing,
        remote_bridge,
    )

    initialize_database()

    definition=copy.deepcopy(homeserver_app_prebuilt.CATALOG["vp3.inventory"])
    definition["version"]="1.2.0-permissions"
    package=homeserver_app_prebuilt._package(definition)

    # Rebuild with governed permissions in the manifest.
    import io,json,zipfile
    source=zipfile.ZipFile(io.BytesIO(package),"r")
    manifest=json.loads(source.read("vp3-app.json").decode("utf-8"))
    manifest["permissions"]=["hardware.camera","network.external","notifications.write"]
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename=="vp3-app.json":
                target.writestr(info,json.dumps(manifest,indent=2,sort_keys=True)+"\n")
            else:
                target.writestr(info,source.read(info.filename))
    source.close()
    package=buffer.getvalue()

    homeserver_apps.ensure_system_app("vp3.inventory","VP3 Inventory",source_ref="vp3-prebuilt:section8")
    release=homeserver_app_packages.install_system_package("vp3.inventory",package)
    assert release["version"]=="1.2.0-permissions"

    status=homeserver_app_security.permission_status("vp3.inventory")
    rows={row["permission"]:row for row in status["permissions"]}
    assert rows["hardware.camera"]["risk"]=="high"
    assert rows["network.external"]["risk"]=="high"
    assert rows["notifications.write"]["risk"]=="medium"
    assert status["allowed_count"]==0
    assert status["denied_count"]==3
    assert status["effective_capabilities"]==[]
    assert status["permission_expansion_requires_review"] is True

    delta=homeserver_app_security.permission_delta(
        "vp3.inventory",
        ["hardware.camera","network.external","notifications.write","hardware.microphone"],
    )
    assert delta["requires_review"] is True
    assert delta["high_risk_added"] is True
    assert [row["permission"] for row in delta["added"]]==["hardware.microphone"]

    try:
        homeserver_app_security.require_permission("vp3.inventory","hardware.camera")
        raise AssertionError("denied capability was executable")
    except homeserver_app_security.AppSecurityError as exc:
        assert exc.status_code==403

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["agent.chat"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    remote_status=remote_bridge.dispatch_remote_request(
        "apps.system.permissions.status",{"app_key":"vp3.inventory"},token
    )
    assert remote_status["ok"] is True
    assert remote_status["payload"]["high_risk_declared"]==2

    granted=remote_bridge.dispatch_remote_request(
        "apps.system.permissions.set",
        {"app_key":"vp3.inventory","permission":"hardware.camera","allowed":True,"reason":"confirmed_section8_test"},
        token,
    )
    assert granted["ok"] is True
    assert homeserver_app_security.permission_allowed("vp3.inventory","hardware.camera") is True
    grant=homeserver_app_security.require_permission("vp3.inventory","hardware.camera")
    assert grant["allowed"] is True and grant["risk"]=="high"

    revoked=remote_bridge.dispatch_remote_request(
        "apps.system.permissions.set",
        {"app_key":"vp3.inventory","permission":"hardware.camera","allowed":False,"reason":"confirmed_section8_revoke"},
        token,
    )
    assert revoked["ok"] is True
    assert homeserver_app_security.permission_allowed("vp3.inventory","hardware.camera") is False

    undeclared=remote_bridge.dispatch_remote_request(
        "apps.system.permissions.set",
        {"app_key":"vp3.inventory","permission":"contacts.write","allowed":True},
        token,
    )
    assert undeclared["ok"] is False
    assert undeclared["status"]==409

    other=pairing.create_pairing_request("other-cloud","Other Cloud",["agent.chat"])
    assert pairing.approve_pairing(other["code"]) is not None
    for operation in ("apps.system.permissions.status","apps.system.permissions.set"):
        try:
            remote_bridge.dispatch_remote_request(
                operation,
                {"app_key":"vp3.inventory","permission":"hardware.camera","allowed":True},
                other["claim_token"],
            )
            raise AssertionError("non-VP3 paired app controlled protected permissions")
        except remote_bridge.RemoteBridgeError:
            pass

    catalog=homeserver_app_security.permission_catalog()
    assert catalog["default_decision"]=="denied"
    assert catalog["grant_authority"]=="explicit_owner_or_confirmed_agent_action"

    cap=homeserver_app_security.public_capability()
    for key in (
        "risk_classification","effective_capability_projection","grant_provenance_events",
        "permission_delta_review","high_risk_permissions_explicit",
    ):
        assert cap[key] is True

    history=homeserver_apps.history("vp3.inventory",100)
    updates=[row for row in history if row["event_type"]=="app.permission.updated"]
    assert len(updates)>=2
    assert any((row.get("metadata") or {}).get("reason")=="confirmed_section8_test" for row in updates)

print("HomeServer System Apps Section 8 permission capability governance: PASS")

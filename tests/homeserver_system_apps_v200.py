from __future__ import annotations

import copy
import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-system-release-v200-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database  # noqa: E402
    from app.services import homeserver_app_packages, homeserver_app_prebuilt, homeserver_apps, pairing, remote_bridge  # noqa: E402

    initialize_database()

    vp3=pairing.create_pairing_request("vp3","VP3 Cloud",["agent.chat"])
    assert pairing.approve_pairing(vp3["code"]) is not None
    token=vp3["claim_token"]

    # Seed a previous protected-system release to prove a real 1.0 -> 1.1 update.
    old=copy.deepcopy(homeserver_app_prebuilt.CATALOG["vp3.notes"])
    old["version"]="1.0.0"
    old["release_notes"]=["Previous stable release."]
    old_package=homeserver_app_prebuilt._package(old)
    old_digest=hashlib.sha256(old_package).hexdigest()
    homeserver_apps.ensure_system_app(
        "vp3.notes","VP3 Notes",
        source_ref="vp3-prebuilt:test-old",
        metadata={"prebuilt_app":True,"vp3_managed":True},
    )
    first=homeserver_app_packages.install_system_package("vp3.notes",old_package)
    assert first["version"]=="1.0.0"
    first_release=first["release_id"]

    status=remote_bridge.dispatch_remote_request("apps.system.release.status",{"app_key":"vp3.notes"},token)
    assert status["ok"] is True
    rs=status["payload"]
    assert rs["contract"]=="vp3.system-app-release-status.v1"
    assert rs["release_channel"]=="stable"
    assert rs["available_version"]=="1.1.0"
    assert rs["compatibility"]["min_homeserver_version"]=="2.4"
    assert len(rs["package_sha256"])==64
    assert rs["integrity"]["trust"]=="embedded_vp3"
    assert rs["runtime"]["installed_version"]=="1.0.0"
    assert rs["rollback_available"] is False

    bad_hash=remote_bridge.dispatch_remote_request(
        "apps.system.install",
        {"app_key":"vp3.notes","expected_version":"1.1.0","expected_sha256":"0"*64,"release_channel":"stable"},
        token,
    )
    assert bad_hash["ok"] is False
    assert bad_hash["status"]==409
    assert homeserver_apps.get("vp3.notes")["installed_version"]=="1.0.0"

    update=remote_bridge.dispatch_remote_request(
        "apps.system.install",
        {
            "app_key":"vp3.notes",
            "expected_version":rs["available_version"],
            "expected_sha256":rs["package_sha256"],
            "release_channel":rs["release_channel"],
        },
        token,
    )
    assert update["ok"] is True,update
    assert update["payload"]["changed"] is True
    assert update["payload"]["installed"] is True
    assert update["payload"]["current"] is True
    assert update["payload"]["verification"]["healthy"] is True
    assert update["payload"]["verification"]["version"]=="1.1.0"
    assert update["payload"]["release"]["previous_release_id"]==first_release
    second_release=update["payload"]["release"]["release_id"]
    assert homeserver_apps.get("vp3.notes")["installed_version"]=="1.1.0"

    after=remote_bridge.dispatch_remote_request("apps.system.release.status",{"app_key":"vp3.notes"},token)
    assert after["payload"]["rollback_available"] is True
    assert after["payload"]["runtime"]["active_release_id"]==second_release
    assert after["payload"]["runtime"]["previous_release_id"]==first_release

    rollback=remote_bridge.dispatch_remote_request(
        "apps.system.rollback",
        {"app_key":"vp3.notes","expected_active_release_id":second_release,"reason":"cloud_release_recovery"},
        token,
    )
    assert rollback["ok"] is True,rollback
    assert rollback["payload"]["changed"] is True
    assert rollback["payload"]["rollback"]["release"]["release_id"]==first_release
    assert rollback["payload"]["rollback"]["release"]["version"]=="1.0.0"
    assert homeserver_apps.get("vp3.notes")["installed_version"]=="1.0.0"

    # Compatibility is enforced before activation.
    incompatible=copy.deepcopy(homeserver_app_prebuilt.CATALOG["vp3.inventory"])
    incompatible["version"]="9.0.0"
    incompatible["min_homeserver_version"]="99.0"
    package=homeserver_app_prebuilt._package(incompatible)
    homeserver_apps.ensure_system_app("vp3.inventory","VP3 Inventory",source_ref="vp3-prebuilt:test")
    try:
        homeserver_app_packages.install_system_package("vp3.inventory",package)
        raise AssertionError("incompatible System App release activated")
    except homeserver_app_packages.AppPackageError as exc:
        assert exc.status_code==409
        assert "requires HomeServer" in str(exc)

    other=pairing.create_pairing_request("other-cloud","Other Cloud",["agent.chat"])
    assert pairing.approve_pairing(other["code"]) is not None
    for operation,payload in (
        ("apps.system.release.status",{"app_key":"vp3.notes"}),
        ("apps.system.rollback",{"app_key":"vp3.notes"}),
    ):
        try:
            remote_bridge.dispatch_remote_request(operation,payload,other["claim_token"])
            raise AssertionError("non-VP3 paired app controlled protected release lifecycle")
        except remote_bridge.RemoteBridgeError:
            pass

    cap=homeserver_app_prebuilt.public_capability()
    for key in (
        "release_metadata","compatibility_gates","sha256_integrity","embedded_trust",
        "post_update_verification","rollback",
    ):
        assert cap[key] is True

    pkg_cap=homeserver_app_packages.public_capability()
    assert pkg_cap["homeserver_compatibility_gate"] is True
    assert pkg_cap["post_activation_verification"] is True

    relay=(ROOT/"relay"/"app.py").read_text(encoding="utf-8")
    assert "apps.system.release.status" in relay
    assert "apps.system.rollback" in relay

print("HomeServer System Apps Section 6 release lifecycle: PASS")

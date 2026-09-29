from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="hosting-v130-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import initialize_database
    from app.services import hosting_cloud_control, hosting_runtime

    initialize_database()

    desired={
        "cloud_site_id":"cloud-site-00000001",
        "revision":1,
        "display_name":"Cloud Bound Site",
        "requested_hostname":"bound.vp3.me",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":123456,
        "sqlite_limit_bytes":65432,
    }
    first=hosting_cloud_control.reconcile(desired)
    assert first["reconcile_result"]=="applied"
    assert first["desired_state"]=="active"
    assert first["observed_state"]=="configured"
    assert first["blocked_reason"]=="deployment_required"
    assert first["public_routing"] is False
    site_id=first["site_id"]
    assert site_id.startswith("site_")

    replay=hosting_cloud_control.reconcile(dict(desired))
    assert replay["reconcile_result"]=="idempotent"
    assert replay["site_id"]==site_id
    assert len(hosting_runtime.list_sites())==1

    stale=dict(desired)
    stale.update({"revision":0})
    try:
        hosting_cloud_control.reconcile(stale)
        raise AssertionError("revision zero accepted")
    except hosting_cloud_control.CloudHostingError:
        pass

    conflict=dict(desired)
    conflict["display_name"]="Conflicting same revision"
    try:
        hosting_cloud_control.reconcile(conflict)
        raise AssertionError("same-revision conflict accepted")
    except hosting_cloud_control.CloudHostingError as exc:
        assert exc.status_code==409

    updated=dict(desired)
    updated.update({"revision":2,"display_name":"Renamed Cloud Site","desired_state":"suspended"})
    second=hosting_cloud_control.reconcile(updated)
    assert second["reconcile_result"]=="applied"
    assert second["observed_state"]=="suspended"
    assert second["display_name"]=="Renamed Cloud Site"
    assert second["site_id"]==site_id

    # Same revision must repair local drift rather than merely acknowledge replay.
    hosting_runtime.set_state(site_id,"active")
    repaired=hosting_cloud_control.reconcile(dict(updated))
    assert repaired["reconcile_result"]=="idempotent"
    assert repaired["observed_state"]=="suspended"

    old=dict(desired)
    old["revision"]=1
    stale_result=hosting_cloud_control.reconcile(old)
    assert stale_result["reconcile_result"]=="stale_ignored"
    assert stale_result["revision"]==2
    assert hosting_runtime.get_site(site_id)["display_name"]=="Renamed Cloud Site"

    runtime_change=dict(updated)
    runtime_change.update({"revision":3,"runtime_kind":"php"})
    try:
        hosting_cloud_control.reconcile(runtime_change)
        raise AssertionError("runtime mutation accepted")
    except hosting_cloud_control.CloudHostingError as exc:
        assert exc.status_code==409

    inventory=hosting_cloud_control.inventory()
    assert inventory["count"]==1
    assert inventory["sites"][0]["cloud_site_id"]=="cloud-site-00000001"
    assert "database_relpath" not in inventory["sites"][0]
    assert "document_root" not in json.dumps(inventory)

    cap=hosting_cloud_control.public_capability()
    assert cap["filesystem_paths_remote"] is False
    assert cap["sqlite_remote_access"] is False
    assert cap["cloud_authoritative_identity"] is True
    assert cap["homeserver_authoritative_runtime"] is True

print("HomeServer Hosting v1.30 Section 4: PASS")

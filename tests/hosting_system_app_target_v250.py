from __future__ import annotations
import os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="hosting-app-target-v1-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import initialize_database
    from app.services import homeserver_app_prebuilt, hosting_cloud_control, hosting_serving, hosting_entitlements
    initialize_database()

    # Install the canonical protected system app first.
    installed=homeserver_app_prebuilt.install("vp3.notes")
    assert installed["app"]["protected_system_app"] is True

    # Grant local hosting capacity through the existing entitlement authority.
    hosting_entitlements.reconcile({
        "contract":"vp3.hosting.entitlements.v1",
        "revision":1,
        "package_key":"test-hosting",
        "max_sites":5,
        "max_active_sites":5,
        "max_public_routes":5,
        "allowed_runtimes":["static","php"],
        "max_storage_bytes_per_site":50_000_000,
        "max_sqlite_bytes_per_site":20_000_000,
    })

    desired={
        "cloud_site_id":"cloud-app-target-0001",
        "revision":1,
        "display_name":"VP3 Notes Hosted",
        "requested_hostname":"notes.example.test",
        "runtime_kind":"static",
        "desired_state":"active",
        "storage_limit_bytes":10_000_000,
        "sqlite_limit_bytes":5_000_000,
        "target_app_key":"vp3.notes",
    }
    result=hosting_cloud_control.reconcile(desired)
    assert result["target_app_key"]=="vp3.notes"
    assert result["observed_state"]=="active"
    assert result["local_serving_ready"] is True
    assert result["active_release_id"] is None

    binding=hosting_cloud_control.binding_for_site(result["site_id"])
    assert binding["target_app_key"]=="vp3.notes"

    response=hosting_serving.serve(result["site_id"],"/")
    assert response.status_code==200
    assert "text/html" in str(response.media_type)

    health=hosting_serving.runtime_health(result["site_id"])
    assert health["target_kind"]=="system_app"
    assert health["target_app_key"]=="vp3.notes"
    assert health["local_serving_ready"] is True

    # User apps may not become public-hosting targets through this bridge.
    from app.services import homeserver_apps
    homeserver_apps.create_user_app("user.demo","User Demo",runtime="static")
    try:
        hosting_cloud_control.reconcile({**desired,"cloud_site_id":"cloud-app-target-0002","revision":1,"target_app_key":"user.demo"})
        raise AssertionError("user app accepted as public hosting target")
    except hosting_cloud_control.CloudHostingError:
        pass

print("HomeServer Hosting system-app target bridge: PASS")

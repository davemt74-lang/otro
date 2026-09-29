from __future__ import annotations

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
            "version":"1.4.0",
            "runtime":"static",
            "entrypoint":"public/index.html",
        }))
        archive.writestr("public/index.html",body)
    return buffer.getvalue()


with tempfile.TemporaryDirectory(prefix="hosting-v140-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import initialize_database
    from app.services import hosting_deployment, hosting_recovery, hosting_runtime

    initialize_database()
    site=hosting_runtime.create_site("Recovery Test",requested_hostname="recover.vp3.me",runtime_kind="static")
    site_id=site["site_id"]
    hosting_deployment.deploy_package(site_id,package("release-one"),request_key="r1")

    storage=hosting_runtime.site_root(site_id)/"storage"
    (storage/"uploads").mkdir(parents=True)
    (storage/"uploads"/"note.txt").write_text("before",encoding="utf-8")
    with hosting_runtime.connect_site_db(site_id) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS demo(value TEXT NOT NULL)")
        connection.execute("INSERT INTO demo(value) VALUES ('before')")
        connection.commit()

    point=hosting_recovery.create_recovery_point(site_id,reason="manual-test")
    assert point["verified"] is True
    assert point["storage_files"]==1
    assert hosting_recovery.verify(site_id,point["recovery_id"])["verified"] is True

    (storage/"uploads"/"note.txt").write_text("after",encoding="utf-8")
    with hosting_runtime.connect_site_db(site_id) as connection:
        connection.execute("DELETE FROM demo")
        connection.execute("INSERT INTO demo(value) VALUES ('after')")
        connection.commit()

    restored=hosting_recovery.restore(site_id,point["recovery_id"])
    assert restored["restored"] is True
    assert restored["pre_restore_recovery_id"].startswith("recovery_")
    assert (storage/"uploads"/"note.txt").read_text(encoding="utf-8")=="before"
    with hosting_runtime.connect_site_db(site_id) as connection:
        assert connection.execute("SELECT value FROM demo").fetchone()[0]=="before"

    health=hosting_recovery.recovery_health(site_id)
    assert health["recovery_points"]>=2
    assert health["latest_verified"] is True
    assert health["retention_limit"]==hosting_recovery.MAX_RECOVERY_POINTS

    manifest=hosting_recovery._manifest_path(site_id,point["recovery_id"])
    data=json.loads(manifest.read_text(encoding="utf-8"))
    data["sqlite"]["sha256"]="0"*64
    manifest.write_text(json.dumps(data),encoding="utf-8")
    try:
        hosting_recovery.restore(site_id,point["recovery_id"])
        raise AssertionError("corrupt recovery point was restored")
    except hosting_recovery.RecoveryError:
        pass
    assert (storage/"uploads"/"note.txt").read_text(encoding="utf-8")=="before"

    cap=hosting_recovery.public_capability()
    assert cap["pre_restore_recovery_point"] is True
    assert cap["remote_restore"] is False
    assert cap["filesystem_paths_remote"] is False

print("HomeServer Hosting v1.40 Section 5: PASS")

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


def package(version:str, migration_sql:str, migration_name:str="001_create_demo.sql")->bytes:
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1",
            "version":version,
            "runtime":"static",
            "entrypoint":"public/index.html",
            "database":{"migrations":[f"database/migrations/{migration_name}"]},
        }))
        archive.writestr("public/index.html",f"release-{version}")
        archive.writestr(f"database/migrations/{migration_name}",migration_sql)
    return buffer.getvalue()


with tempfile.TemporaryDirectory(prefix="hosting-v150-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import initialize_database
    from app.services import hosting_deployment, hosting_runtime, hosting_sqlite

    initialize_database()

    one=hosting_runtime.create_site(
        "SQLite One",
        requested_hostname="one.vp3.me",
        runtime_kind="static",
        sqlite_limit_bytes=5_000_000,
    )
    two=hosting_runtime.create_site(
        "SQLite Two",
        requested_hostname="two.vp3.me",
        runtime_kind="static",
        sqlite_limit_bytes=5_000_000,
    )

    sql="CREATE TABLE demo(id INTEGER PRIMARY KEY, value TEXT NOT NULL);\nINSERT INTO demo(value) VALUES ('one');\n"
    release=hosting_deployment.deploy_package(one["site_id"],package("1.5.0",sql),request_key="sqlite-v150-a")
    assert release["active"] is True
    assert release["sqlite_migrations"]["applied"]==["001_create_demo.sql"]
    assert release["sqlite_migrations"]["recovery_id"].startswith("recovery_")

    connection=hosting_runtime.connect_site_db(one["site_id"])
    try:
        assert connection.execute("SELECT value FROM demo").fetchone()[0]=="one"
    finally:
        connection.close()

    connection=hosting_runtime.connect_site_db(two["site_id"])
    try:
        try:
            connection.execute("SELECT value FROM demo").fetchone()
            raise AssertionError("site databases were not isolated")
        except Exception as exc:
            assert "no such table" in str(exc).lower()
    finally:
        connection.close()

    repeat=hosting_deployment.deploy_package(one["site_id"],package("1.5.1",sql),request_key="sqlite-v150-b")
    assert repeat["sqlite_migrations"]["applied"]==[]
    assert repeat["sqlite_migrations"]["skipped"]==["001_create_demo.sql"]
    assert repeat["sqlite_migrations"]["recovery_id"] is None

    active_before=hosting_deployment.deployment_status(one["site_id"])["active_release_id"]
    changed="CREATE TABLE demo(id INTEGER PRIMARY KEY, changed TEXT);"
    try:
        hosting_deployment.deploy_package(one["site_id"],package("1.5.2",changed),request_key="sqlite-v150-c")
        raise AssertionError("changed migration checksum was accepted")
    except hosting_sqlite.SQLiteRuntimeError:
        pass
    assert hosting_deployment.deployment_status(one["site_id"])["active_release_id"]==active_before

    try:
        hosting_sqlite.apply_migrations(one["site_id"],[{
            "name":"002_escape.sql",
            "sql":"ATTACH DATABASE '/tmp/other.sqlite' AS other;",
        }],release_id="release_test")
        raise AssertionError("filesystem-capable migration SQL was accepted")
    except hosting_sqlite.SQLiteRuntimeError:
        pass

    status=hosting_sqlite.schema_status(one["site_id"])
    assert status["healthy"] is True
    assert status["migration_count"]==1
    assert status["latest_migration"]=="001_create_demo.sql"
    assert "path" not in json.dumps(status).lower()
    assert "sha256" not in json.dumps(status).lower()

    capability=hosting_sqlite.public_capability()
    assert capability["per_site_database"] is True
    assert capability["serialized_migrations"] is True
    assert capability["migration_checksum_pinning"] is True
    assert capability["pre_migration_recovery_point"] is True
    assert capability["cloud_raw_sql"] is False
    assert capability["cloud_database_path"] is False
    assert capability["automatic_down_migrations"] is False

print("HomeServer Hosting v1.50 Section 6: PASS")

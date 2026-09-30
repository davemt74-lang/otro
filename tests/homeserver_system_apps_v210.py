from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-system-data-v210-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import initialize_database
    from app.services import (
        homeserver_app_data_lifecycle,
        homeserver_app_packages,
        homeserver_app_prebuilt,
        homeserver_app_resources,
        homeserver_apps,
    )

    initialize_database()

    old=copy.deepcopy(homeserver_app_prebuilt.CATALOG["vp3.notes"])
    old["version"]="1.1.0"
    old["data_schema_version"]="1"
    old["data_migration_reversible"]=True
    old["release_notes"]=["Section 6 baseline."]
    homeserver_apps.ensure_system_app("vp3.notes","VP3 Notes",source_ref="vp3-prebuilt:section6")
    first=homeserver_app_packages.install_system_package("vp3.notes",homeserver_app_prebuilt._package(old))
    first_release=first["release_id"]

    homeserver_app_resources.write_file("vp3.notes","data.json",json.dumps([{"title":"Keep me","body":"persistent"}]).encode())
    db_path=homeserver_app_resources.sqlite_path("vp3.notes","app.db")
    con=sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS original_data (id INTEGER PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO original_data(value) VALUES ('preserve')")
    con.commit()
    con.close()

    before=homeserver_app_data_lifecycle.status("vp3.notes")
    assert before["schema_version"]=="1"

    update=homeserver_app_prebuilt.install("vp3.notes")
    assert update["changed"] is True
    release=update["release"]
    migration=release["data_migration"]
    assert migration["migration_required"] is True
    assert migration["migration_reversible"] is True
    assert migration["from_schema_version"]=="1"
    assert migration["to_schema_version"]=="2"
    assert migration["snapshot_id"].startswith("appsnap_")
    assert migration["rollback_safe"] is True
    assert "002_section7.sql" in migration["migration_scripts"]

    after=homeserver_app_data_lifecycle.status("vp3.notes")
    assert after["schema_version"]=="2"
    assert after["last_snapshot_id"]==migration["snapshot_id"]
    con=sqlite3.connect(db_path)
    assert con.execute("SELECT value FROM original_data").fetchone()[0]=="preserve"
    assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vp3_section7_migration_marker'").fetchone()
    con.close()

    current_release=release["release_id"]
    rollback=homeserver_app_prebuilt.rollback(
        "vp3.notes",
        expected_active_release_id=current_release,
        reason="section7_test",
    )
    assert rollback["changed"] is True
    assert rollback["rollback"]["release"]["release_id"]==first_release
    assert rollback["data_restore"]["restored"] is True
    assert homeserver_apps.get("vp3.notes")["installed_version"]=="1.1.0"
    restored=homeserver_app_data_lifecycle.status("vp3.notes")
    assert restored["schema_version"]=="1"
    assert homeserver_app_resources.read_file("vp3.notes","data.json")==json.dumps([{"title":"Keep me","body":"persistent"}]).encode()
    con=sqlite3.connect(db_path)
    assert con.execute("SELECT value FROM original_data").fetchone()[0]=="preserve"
    assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vp3_section7_migration_marker'").fetchone() is None
    con.close()

    # An explicitly irreversible migration must block rollback before changing package or data state.
    irreversible=copy.deepcopy(homeserver_app_prebuilt.CATALOG["vp3.notes"])
    irreversible["version"]="1.3.0-test"
    irreversible["data_schema_version"]="3"
    irreversible["data_migration_reversible"]=False
    irreversible_package=homeserver_app_prebuilt._package(irreversible)
    third=homeserver_app_packages.install_system_package("vp3.notes",irreversible_package)
    assert third["data_migration"]["migration_required"] is True
    assert third["data_migration"]["rollback_safe"] is False
    active_before_block=homeserver_app_packages.runtime_status("vp3.notes")["active_release_id"]
    try:
        homeserver_app_prebuilt.rollback("vp3.notes",expected_active_release_id=active_before_block,reason="must_block")
        raise AssertionError("irreversible data migration rollback was allowed")
    except homeserver_apps.HomeServerAppError as exc:
        assert exc.status_code==409
        assert "irreversible" in str(exc).lower()
    assert homeserver_app_packages.runtime_status("vp3.notes")["active_release_id"]==active_before_block
    assert homeserver_app_data_lifecycle.status("vp3.notes")["schema_version"]=="3"

    cap=homeserver_app_data_lifecycle.public_capability()
    assert cap["pre_migration_snapshot"] is True
    assert cap["snapshot_sha256_integrity"] is True
    assert cap["reversible_migration_rollback"] is True
    assert cap["irreversible_migration_rollback_block"] is True
    assert cap["automatic_restore_on_migration_failure"] is True

print("HomeServer System Apps Section 7 data migration and recovery lifecycle: PASS")

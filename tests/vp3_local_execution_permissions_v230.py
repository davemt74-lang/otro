from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if not os.environ.get("HOMESERVER_DATA_DIR"):
    raise SystemExit("HOMESERVER_DATA_DIR is required")

from app.database import db, initialize_database  # noqa: E402

PERMISSIONS = {"files.read", "files.write", "devices.read", "devices.control"}
MIGRATION = ROOT / "database" / "migrations" / "032_vp3_local_execution_permissions.sql"

initialize_database()

with db() as connection:
    connection.execute("DELETE FROM app_permissions")
    connection.execute("DELETE FROM paired_apps")
    connection.execute(
        "INSERT INTO paired_apps(app_key,name,status,token_hash) VALUES ('vp3','VP3','active',?)",
        ("a" * 64,),
    )
    connection.execute(
        "INSERT INTO paired_apps(app_key,name,status,token_hash) VALUES ('third-party','Third Party','active',?)",
        ("b" * 64,),
    )
    connection.execute(
        "INSERT INTO paired_apps(app_key,name,status,token_hash) VALUES ('vp3-revoked','Old VP3','revoked',?)",
        ("c" * 64,),
    )
    vp3_id = int(connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()["id"])
    third_id = int(connection.execute("SELECT id FROM paired_apps WHERE app_key='third-party'").fetchone()["id"])
    connection.execute(
        "INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES (?,?,0)",
        (vp3_id, "files.read"),
    )

sql = MIGRATION.read_text(encoding="utf-8")
with db() as connection:
    connection.executescript(sql)
    # Repeat safety is a release requirement.
    connection.executescript(sql)

with db() as connection:
    rows = connection.execute(
        "SELECT permission,allowed FROM app_permissions WHERE paired_app_id=? ORDER BY permission",
        (vp3_id,),
    ).fetchall()
    granted = {str(row["permission"]) for row in rows if int(row["allowed"]) == 1}
    assert PERMISSIONS <= granted, (PERMISSIONS, granted)

    third = connection.execute(
        "SELECT permission FROM app_permissions WHERE paired_app_id=? AND permission IN ('files.read','files.write','devices.read','devices.control')",
        (third_id,),
    ).fetchall()
    assert third == [], "v2.3 scope migration must not grant local execution to third-party apps"

    count = connection.execute(
        "SELECT COUNT(*) AS total FROM app_permissions WHERE paired_app_id=? AND permission IN ('files.read','files.write','devices.read','devices.control')",
        (vp3_id,),
    ).fetchone()
    assert int(count["total"]) == 4, "migration must be repeat-safe without duplicate permissions"

print("HomeServer v2.3 Section 4 VP3 permission migration: PASS")

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="hosting-v100-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import db, initialize_database
    from app.services import hosting_runtime

    initialize_database()
    with db() as connection:
        versions=[row["version"] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
    assert versions==list(range(1,61))

    site=hosting_runtime.create_site(
        "Pizza Site",
        requested_hostname="pizza.vp3.me",
        runtime_kind="php",
        storage_limit_bytes=20_000_000,
        sqlite_limit_bytes=10_000_000,
    )
    assert site["site_id"].startswith("site_")
    assert site["state"]=="configured"
    assert site["database_kind"]=="sqlite"

    root=hosting_runtime.site_root(site["site_id"])
    assert (root/"public").is_dir()
    assert (root/"storage").is_dir()
    assert (root/"database"/"site.sqlite").is_file()
    assert (root/"manifest.json").is_file()

    health=hosting_runtime.database_health(site["site_id"])
    assert health["healthy"] is True
    assert health["journal_mode"]=="wal"
    assert health["foreign_keys"] is True
    assert health["busy_timeout_ms"]>=30_000

    usage=hosting_runtime.sample_usage(site["site_id"])
    assert usage["storage_bytes"]>=0
    assert usage["sqlite_bytes"]>0

    backup=hosting_runtime.create_backup(site["site_id"])
    assert backup["sha256"]
    assert (root/"backups"/f"{backup['backup_id']}.sqlite").is_file()

    active=hosting_runtime.set_state(site["site_id"],"active")
    assert active["state"]=="active"

    with db() as connection:
        usage_before=connection.execute("SELECT COUNT(*) FROM hosting_usage_samples WHERE site_id=?",(site["site_id"],)).fetchone()[0]
    context=hosting_runtime.agent_context_fragment("How is my hosting and SQLite database?")
    assert "pizza.vp3.me" in context
    assert "SQLite healthy" in context
    assert str(root) not in context
    with db() as connection:
        usage_after=connection.execute("SELECT COUNT(*) FROM hosting_usage_samples WHERE site_id=?",(site["site_id"],)).fetchone()[0]
    assert usage_after==usage_before

    capability=hosting_runtime.public_capability()
    assert capability["per_site_sqlite"] is True
    assert capability["cloud_authoritative_subdomains"] is True
    assert capability["public_routing"] is False
    assert capability["cpanel_credentials"] is False

print("HomeServer Hosting v1.00 Section 1: PASS")

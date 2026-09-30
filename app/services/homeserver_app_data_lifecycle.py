from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from . import homeserver_app_resources, homeserver_apps

CONTRACT = "vp3.app.data-lifecycle.v1"
MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024


class AppDataLifecycleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _data_root(app_key: str) -> Path:
    app = homeserver_apps.get(app_key)
    root = settings.data_dir / "app-data" / str(app["app_id"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _snapshot_root(app_key: str) -> Path:
    app = homeserver_apps.get(app_key)
    root = settings.data_dir / "app-recovery" / str(app["app_id"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    count = 0
    if not root.exists():
        return digest.hexdigest(), 0, 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AppDataLifecycleError("App data contains an unsupported symbolic link.", 500)
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix().encode("utf-8")
        blob = path.read_bytes()
        size += len(blob)
        count += 1
        if size > MAX_SNAPSHOT_BYTES:
            raise AppDataLifecycleError("App data exceeds the recovery snapshot limit.", 413)
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(len(blob).to_bytes(8, "big"))
        digest.update(blob)
    return digest.hexdigest(), size, count


def create_snapshot(app_key: str, *, reason: str, schema_version: str) -> dict[str, Any]:
    app = homeserver_apps.get(app_key)
    source = _data_root(app_key)
    snapshot_id = "appsnap_" + uuid.uuid4().hex[:24]
    root = _snapshot_root(app_key)
    staging = root / (snapshot_id + ".tmp")
    final = root / snapshot_id
    staging.mkdir(parents=True, exist_ok=False)
    try:
        payload = staging / "data"
        if source.exists():
            shutil.copytree(source, payload, symlinks=False, dirs_exist_ok=True)
        else:
            payload.mkdir(parents=True)
        digest, size, count = _tree_digest(payload)
        metadata = {
            "contract": CONTRACT,
            "snapshot_id": snapshot_id,
            "app_key": app_key,
            "schema_version": str(schema_version or "1"),
            "reason": str(reason or "pre_migration")[:120],
            "sha256": digest,
            "bytes": size,
            "files": count,
        }
        (staging / "snapshot.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staging, final)
        with db() as connection:
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.data.snapshot.created','system','vp3_release',?)""",
                (app["app_id"], json.dumps(metadata, separators=(",", ":"), sort_keys=True)),
            )
        return metadata
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _snapshot(app_key: str, snapshot_id: str) -> tuple[Path, dict[str, Any]]:
    sid = str(snapshot_id or "").strip()
    if not sid.startswith("appsnap_") or len(sid) > 80:
        raise AppDataLifecycleError("App recovery snapshot id is invalid.")
    root = _snapshot_root(app_key) / sid
    meta_path = root / "snapshot.json"
    if not root.is_dir() or not meta_path.is_file() or root.is_symlink():
        raise AppDataLifecycleError("App recovery snapshot was not found.", 404)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppDataLifecycleError("App recovery snapshot metadata is invalid.", 500) from exc
    if not isinstance(meta, dict) or meta.get("app_key") != app_key or meta.get("snapshot_id") != sid:
        raise AppDataLifecycleError("App recovery snapshot metadata is invalid.", 500)
    return root, meta


def restore_snapshot(app_key: str, snapshot_id: str, *, reason: str = "release_rollback") -> dict[str, Any]:
    app = homeserver_apps.get(app_key)
    root, meta = _snapshot(app_key, snapshot_id)
    payload = root / "data"
    expected = str(meta.get("sha256") or "")
    actual, _, _ = _tree_digest(payload)
    if not expected or actual != expected:
        raise AppDataLifecycleError("App recovery snapshot integrity check failed.", 409)
    target = _data_root(app_key)
    recovery = target.with_name(target.name + ".restore-" + uuid.uuid4().hex[:8])
    if recovery.exists():
        shutil.rmtree(recovery)
    shutil.copytree(payload, recovery, symlinks=False)
    old = target.with_name(target.name + ".old-" + uuid.uuid4().hex[:8])
    try:
        if target.exists():
            os.replace(target, old)
        os.replace(recovery, target)
        if old.exists():
            shutil.rmtree(old)
    except Exception:
        if recovery.exists():
            shutil.rmtree(recovery, ignore_errors=True)
        if old.exists() and not target.exists():
            os.replace(old, target)
        raise
    metadata = {
        "snapshot_id": snapshot_id,
        "schema_version": str(meta.get("schema_version") or "1"),
        "reason": str(reason or "release_rollback")[:120],
        "sha256": expected,
    }
    with db() as connection:
        row = connection.execute("SELECT metadata_json FROM homeserver_apps WHERE app_id=?", (app["app_id"],)).fetchone()
        app_meta = json.loads((row["metadata_json"] if row else "{}") or "{}")
        app_meta["data_schema_version"] = metadata["schema_version"]
        app_meta["last_data_restore_snapshot_id"] = snapshot_id
        connection.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?",
            (json.dumps(app_meta, separators=(",", ":"), sort_keys=True), app["app_id"]),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.data.snapshot.restored','system','vp3_release',?)""",
            (app["app_id"], json.dumps(metadata, separators=(",", ":"), sort_keys=True)),
        )
    return {"contract": CONTRACT, "restored": True, **metadata}


def _run_system_sql_migrations(app_key: str, content_root: Path, relative_dir: str) -> list[str]:
    app = homeserver_apps.get(app_key)
    if app["app_class"] != "system" or not app["protected_system_app"]:
        raise AppDataLifecycleError("Package data migrations are restricted to protected VP3 system apps.", 409)
    migration_root = (content_root / relative_dir).resolve()
    if content_root.resolve() not in migration_root.parents and migration_root != content_root.resolve():
        raise AppDataLifecycleError("App migration path escaped its release root.", 409)
    if not migration_root.exists():
        return []
    scripts = sorted(path for path in migration_root.rglob("*.sql") if path.is_file() and not path.is_symlink())
    if len(scripts) > 100:
        raise AppDataLifecycleError("App release contains too many data migration scripts.", 413)
    if not scripts:
        return []
    database = homeserver_app_resources.sqlite_path(app_key, "app.db")
    executed: list[str] = []
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for script in scripts:
            sql = script.read_text(encoding="utf-8")
            if len(sql.encode("utf-8")) > 1024 * 1024:
                raise AppDataLifecycleError("App data migration script is too large.", 413)
            upper = sql.upper()
            for forbidden in ("ATTACH DATABASE", "DETACH DATABASE", "LOAD_EXTENSION", "WRITABLE_SCHEMA", "VACUUM INTO"):
                if forbidden in upper:
                    raise AppDataLifecycleError("App data migration contains an unsupported SQLite operation.", 409)
            connection.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
            executed.append(script.relative_to(migration_root).as_posix())
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        connection.close()
    homeserver_app_resources.enforce_sqlite_quota(app_key)
    return executed


def prepare_and_apply_migration(
    app_key: str,
    manifest: dict[str, Any],
    content_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    app = homeserver_apps.get(app_key)
    app_meta = dict(app.get("metadata") or {})
    current_schema = str(app_meta.get("data_schema_version") or "1")
    target_schema = str(manifest.get("data_schema_version") or current_schema or "1")
    required = target_schema != current_schema and bool(app.get("installed_version"))
    reversible = bool(manifest.get("data_migration_reversible", True))
    snapshot = None
    scripts: list[str] = []
    if required:
        snapshot = create_snapshot(app_key, reason="pre_release_data_migration", schema_version=current_schema)
        relative = str(manifest.get("database_migrations") or "").strip()
        try:
            if relative:
                scripts = _run_system_sql_migrations(app_key, content_root, relative)
        except Exception:
            restore_snapshot(app_key, str(snapshot["snapshot_id"]), reason="migration_failed")
            raise
    result = {
        "contract": CONTRACT,
        "release_id": release_id,
        "from_schema_version": current_schema,
        "to_schema_version": target_schema,
        "migration_required": required,
        "migration_reversible": reversible,
        "snapshot_id": str((snapshot or {}).get("snapshot_id") or ""),
        "migration_scripts": scripts,
        "rollback_safe": (not required) or (reversible and snapshot is not None),
    }
    app = homeserver_apps.get(app_key)
    metadata = dict(app.get("metadata") or {})
    metadata.update({
        "data_schema_version": target_schema,
        "last_data_migration_release_id": release_id if required else str(metadata.get("last_data_migration_release_id") or ""),
        "last_data_snapshot_id": result["snapshot_id"] if snapshot else str(metadata.get("last_data_snapshot_id") or ""),
        "last_data_migration_reversible": reversible,
    })
    with db() as connection:
        connection.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_key=?",
            (json.dumps(metadata, separators=(",", ":"), sort_keys=True), app_key),
        )
        if required:
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.data.migrated','system','vp3_release',?)""",
                (app["app_id"], json.dumps(result, separators=(",", ":"), sort_keys=True)),
            )
    return result


def rollback_data_for_active_release(app_key: str, active_release: dict[str, Any]) -> dict[str, Any]:
    migration = dict(active_release.get("data_migration") or {})
    if not migration.get("migration_required"):
        return {"contract": CONTRACT, "restored": False, "reason": "no_data_migration"}
    if not migration.get("migration_reversible"):
        raise AppDataLifecycleError(
            "Rollback is blocked because the active release contains an irreversible app data migration.",
            409,
        )
    snapshot_id = str(migration.get("snapshot_id") or "")
    if not snapshot_id:
        raise AppDataLifecycleError("Rollback is blocked because the required app data recovery snapshot is unavailable.", 409)
    return restore_snapshot(app_key, snapshot_id, reason="release_rollback")


def status(app_key: str) -> dict[str, Any]:
    app = homeserver_apps.get(app_key)
    metadata = dict(app.get("metadata") or {})
    return {
        "contract": CONTRACT,
        "app_key": app_key,
        "schema_version": str(metadata.get("data_schema_version") or "1"),
        "last_migration_release_id": str(metadata.get("last_data_migration_release_id") or ""),
        "last_snapshot_id": str(metadata.get("last_data_snapshot_id") or ""),
        "last_restore_snapshot_id": str(metadata.get("last_data_restore_snapshot_id") or ""),
        "last_migration_reversible": bool(metadata.get("last_data_migration_reversible", True)),
    }


def public_capability() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "isolated_app_data_snapshots": True,
        "snapshot_sha256_integrity": True,
        "pre_migration_snapshot": True,
        "protected_system_sql_migrations": True,
        "schema_version_tracking": True,
        "reversible_migration_rollback": True,
        "irreversible_migration_rollback_block": True,
        "automatic_restore_on_migration_failure": True,
        "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
    }

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from ..config import settings
from ..database import db, migration_files
from . import backups
from .owner_secret import owner_secret_metadata
from .providers import ProviderError, get_ollama
from .runtime_control import runtime_control_available
from .windows_integration import startup_state


def _read_setting(key: str, default):
    with db() as connection:
        row = connection.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=? LIMIT 1",
            (key,),
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _write_setting(key: str, value) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with db() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key, value_json)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, encoded),
        )


def first_run_status() -> dict:
    with db() as connection:
        agent = connection.execute(
            "SELECT name, instructions, model FROM agents WHERE is_primary=1 LIMIT 1"
        ).fetchone()
        provider = connection.execute(
            "SELECT model, enabled FROM model_providers WHERE provider_key='ollama' LIMIT 1"
        ).fetchone()
        app_count = connection.execute(
            "SELECT COUNT(*) FROM paired_apps WHERE status='active'"
        ).fetchone()[0]
    backup_count = len(backups.list_backups())
    agent_configured = bool(
        agent
        and (
            str(agent["name"] or "").strip() not in {"", "HomeServer Agent"}
            or str(agent["model"] or "").strip()
            or str(agent["instructions"] or "").strip() not in {"", "Primary local HomeServer agent."}
        )
    )
    provider_configured = bool(provider and provider["enabled"] and str(provider["model"] or "").strip())
    return {
        "complete": bool(_read_setting("first_run_complete", False)),
        "prompted": bool(_read_setting("first_run_prompted", False)),
        "steps": {
            "agent_configured": agent_configured,
            "local_model_enabled": provider_configured,
            "app_connected": app_count > 0,
            "backup_created": backup_count > 0,
        },
    }


def set_first_run_complete(complete: bool = True) -> dict:
    _write_setting("first_run_complete", bool(complete))
    _write_setting("first_run_prompted", True)
    return first_run_status()


def mark_first_run_prompted() -> None:
    _write_setting("first_run_prompted", True)


def _bootstrap_state() -> dict | None:
    path = settings.bootstrap_state_path
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _database_diagnostics() -> dict:
    result = {
        "ok": False,
        "quick_check": "unavailable",
        "foreign_key_violations": None,
        "schema_version": None,
        "supported_schema_version": max([1, *(version for version, _ in migration_files())]),
    }
    try:
        connection = sqlite3.connect(settings.db_path, timeout=3)
        try:
            quick_rows = connection.execute("PRAGMA quick_check").fetchall()
            quick = [str(row[0]) for row in quick_rows]
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            schema_row = connection.execute("SELECT COALESCE(MAX(version), 1) FROM schema_migrations").fetchone()
            result.update(
                {
                    "ok": quick == ["ok"] and not violations,
                    "quick_check": "ok" if quick == ["ok"] else "; ".join(quick[:5]),
                    "foreign_key_violations": len(violations),
                    "schema_version": int(schema_row[0]) if schema_row else 1,
                }
            )
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as exc:
        result["quick_check"] = f"error:{type(exc).__name__}"
    return result


def _ollama_diagnostics() -> dict:
    try:
        provider = get_ollama()
    except ProviderError:
        return {"configured": False, "enabled": False, "reachable": False, "model": None}

    reachable = False
    base_url = str(provider.get("base_url") or "")
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.45):
            reachable = True
    except OSError:
        pass
    return {
        "configured": bool(str(provider.get("model") or "").strip()),
        "enabled": bool(provider.get("enabled")),
        "reachable": reachable,
        "model": provider.get("model") or None,
        "base_url": base_url,
    }


def diagnostics() -> dict:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(settings.data_dir)
    try:
        backup_items = backups.list_backups()
        backup_error = None
    except Exception as exc:
        backup_items = []
        backup_error = type(exc).__name__
    try:
        pending_restore = backups.pending_restore_info()
    except Exception:
        pending_restore = {"valid": False, "error": "restore-status-unavailable"}

    return {
        "version": settings.version,
        "endpoint": f"http://{settings.host}:{settings.port}",
        "data": {
            "path": str(settings.data_dir),
            "db_path": str(settings.db_path),
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "bootstrap": _bootstrap_state(),
        },
        "database": _database_diagnostics(),
        "ollama": _ollama_diagnostics(),
        "backups": {
            "count": len(backup_items),
            "latest": backup_items[0] if backup_items else None,
            "pending_restore": pending_restore,
            "error": backup_error,
        },
        "startup": startup_state(),
        "owner_security": owner_secret_metadata(),
        "runtime_control": {"available": runtime_control_available()},
    }


def system_summary() -> dict:
    return {"setup": first_run_status(), "diagnostics": diagnostics()}

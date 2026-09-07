from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import MutableMapping


def preferred_windows_data_dir(local_app_data: str | Path | None = None) -> Path:
    if local_app_data is None:
        local_app_data = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "HomeServer" / "Data"


def legacy_data_dir(home: str | Path | None = None) -> Path:
    return Path(home) / ".homeserver" if home is not None else Path.home() / ".homeserver"


def _has_payload(path: Path) -> bool:
    try:
        return path.exists() and any(path.iterdir())
    except OSError:
        return True


def _write_state(data_dir: Path, payload: dict) -> None:
    try:
        runtime_dir = data_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        target = runtime_dir / "bootstrap-state.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        # Bootstrap diagnostics are useful but must never prevent HomeServer from starting.
        pass


def prepare_data_directory(
    *,
    platform_name: str | None = None,
    home: str | Path | None = None,
    local_app_data: str | Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> dict:
    env = environ if environ is not None else os.environ
    platform = platform_name or os.name
    now = datetime.now(timezone.utc).isoformat()

    explicit = str(env.get("HOMESERVER_DATA_DIR") or "").strip()
    if explicit:
        chosen = Path(explicit).expanduser()
        chosen.mkdir(parents=True, exist_ok=True)
        state = {
            "checked_at": now,
            "mode": "explicit",
            "data_dir": str(chosen),
            "migration": "not_applicable",
            "warning": None,
        }
        _write_state(chosen, state)
        return state

    legacy = legacy_data_dir(home)
    if platform != "nt":
        legacy.mkdir(parents=True, exist_ok=True)
        env["HOMESERVER_DATA_DIR"] = str(legacy)
        state = {
            "checked_at": now,
            "mode": "legacy_non_windows",
            "data_dir": str(legacy),
            "migration": "not_applicable",
            "warning": None,
        }
        _write_state(legacy, state)
        return state

    preferred = preferred_windows_data_dir(local_app_data)
    preferred_payload = _has_payload(preferred)
    legacy_payload = _has_payload(legacy)
    migration = "not_needed"
    warning: str | None = None

    if legacy_payload and not preferred_payload:
        try:
            preferred.parent.mkdir(parents=True, exist_ok=True)
            if preferred.exists() and not _has_payload(preferred):
                preferred.rmdir()
            shutil.move(str(legacy), str(preferred))
            migration = "migrated_legacy"
        except OSError as exc:
            chosen = legacy
            chosen.mkdir(parents=True, exist_ok=True)
            env["HOMESERVER_DATA_DIR"] = str(chosen)
            state = {
                "checked_at": now,
                "mode": "legacy_fallback",
                "data_dir": str(chosen),
                "preferred_data_dir": str(preferred),
                "legacy_data_dir": str(legacy),
                "migration": "failed",
                "warning": f"Legacy data could not be moved to LocalAppData: {type(exc).__name__}",
            }
            _write_state(chosen, state)
            return state
    elif legacy_payload and preferred_payload:
        migration = "conflict_preserved"
        warning = "Both the legacy and LocalAppData HomeServer folders contain data. LocalAppData is active; the legacy folder was left unchanged."

    preferred.mkdir(parents=True, exist_ok=True)
    env["HOMESERVER_DATA_DIR"] = str(preferred)
    state = {
        "checked_at": now,
        "mode": "windows_localappdata",
        "data_dir": str(preferred),
        "preferred_data_dir": str(preferred),
        "legacy_data_dir": str(legacy),
        "migration": migration,
        "warning": warning,
    }
    _write_state(preferred, state)
    return state

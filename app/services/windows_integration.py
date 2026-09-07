from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "HomeServer"


class WindowsIntegrationError(RuntimeError):
    pass


def _is_frozen_windows() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _quoted_executable() -> str:
    return f'"{Path(sys.executable).resolve()}"'


def _legacy_startup_shortcut() -> Path | None:
    if os.name != "nt":
        return None
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "HomeServer.lnk"


def startup_state() -> dict:
    supported = _is_frozen_windows()
    command: str | None = None
    registry_enabled = False
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, _RUN_VALUE)
                command = str(value)
                registry_enabled = bool(command.strip())
        except (FileNotFoundError, OSError):
            pass
    shortcut = _legacy_startup_shortcut()
    legacy_enabled = bool(shortcut and shortcut.is_file())
    return {
        "supported": supported,
        "enabled": registry_enabled or legacy_enabled,
        "registry_enabled": registry_enabled,
        "legacy_shortcut": legacy_enabled,
        "mode": "registry" if registry_enabled else ("legacy-shortcut" if legacy_enabled else "disabled"),
        "command": command,
        "executable": str(Path(sys.executable).resolve()) if _is_frozen_windows() else None,
    }


def set_startup_enabled(enabled: bool) -> dict:
    if not _is_frozen_windows():
        raise WindowsIntegrationError("Start with Windows can be changed only from the installed Windows application.")

    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _quoted_executable())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass
        shortcut = _legacy_startup_shortcut()
        if shortcut is not None:
            shortcut.unlink(missing_ok=True)
    except OSError as exc:
        raise WindowsIntegrationError("Windows startup registration could not be updated.") from exc
    return startup_state()


def open_folder(path: str | Path) -> None:
    target = Path(path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        raise WindowsIntegrationError("The data folder could not be opened.") from exc

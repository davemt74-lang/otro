from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "HomeServer"
_FOLDER_PICKER_LOCK = threading.Lock()


class WindowsIntegrationError(RuntimeError):
    pass


def _is_frozen_windows() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _quoted_executable() -> str:
    return f'"{Path(sys.executable).resolve()}"'


def _startup_command() -> str:
    return f"{_quoted_executable()} --background"


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
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
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


def native_folder_picker_supported() -> bool:
    """Return whether this runtime can open the owner-visible native folder picker."""
    return os.name == "nt"


def pick_local_folder(title: str = "Select a folder for HomeServer Knowledge") -> Path | None:
    """Open a Windows-native folder dialog without exposing its path to a remote caller.

    The picker executes only on the HomeServer machine. A paired cloud app can
    trigger this local owner interaction, but the selected absolute path never
    leaves HomeServer; callers receive only the safe knowledge-source mapping.
    Only one picker may be open at once so a paired app cannot stack dialogs.
    """
    if os.name != "nt":
        raise WindowsIntegrationError(
            "Native folder mapping requires the HomeServer Windows desktop application."
        )
    if not _FOLDER_PICKER_LOCK.acquire(blocking=False):
        raise WindowsIntegrationError("A HomeServer folder picker is already open.")

    env = os.environ.copy()
    env["HOMESERVER_FOLDER_PICKER_TITLE"] = str(title or "Select a folder")[:200]
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:HOMESERVER_FOLDER_PICKER_TITLE
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.SelectedPath
    exit 0
}
exit 3
""".strip()

    try:
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check=False,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WindowsIntegrationError("The native folder picker could not be opened.") from exc
    finally:
        _FOLDER_PICKER_LOCK.release()

    if result.returncode == 3:
        return None
    if result.returncode != 0:
        raise WindowsIntegrationError("The native folder picker could not be completed.")

    selected = result.stdout.strip()
    if not selected:
        return None
    try:
        path = Path(selected).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WindowsIntegrationError("The selected local folder could not be resolved.") from exc
    if not path.is_dir():
        raise WindowsIntegrationError("The selected knowledge source must be a folder.")
    return path


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

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.single_instance import SingleInstance  # noqa: E402


with tempfile.TemporaryDirectory(prefix="homeserver-single-instance-") as data_dir:
    first = SingleInstance(data_dir)
    second = SingleInstance(data_dir)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()

    third = SingleInstance(data_dir)
    assert third.acquire() is True
    third.release()

launcher = (ROOT_DIR / "desktop" / "launcher.py").read_text(encoding="utf-8")
windows_integration = (ROOT_DIR / "app" / "services" / "windows_integration.py").read_text(encoding="utf-8")
installer = (ROOT_DIR / "installer" / "HomeServer.iss").read_text(encoding="utf-8")

# Explicit user launches must open a visible workspace, including when the
# named mutex indicates HomeServer is already running.
assert "def _schedule_initial_open(self) -> None:" in launcher
assert "self._schedule_initial_open()" in launcher
assert "threading.Timer(0.6, self.open_control_center).start()" in launcher
assert "threading.Timer(0.6, self.open_system).start()" in launcher
assert 'if "--headless" in sys.argv or "--background" in sys.argv:' in launcher
assert "_open(_path_for_health(health))" in launcher

# Windows sign-in startup stays background-only rather than opening a browser
# every login, while normal shortcuts/post-install launches remain interactive.
assert 'return f"{_quoted_executable()} --background"' in windows_integration
assert "winreg.REG_SZ, _startup_command()" in windows_integration
assert 'ValueData: """{app}\\{#MyAppExeName}"" --background"' in installer

# Windows shell URL activation is preferred for a packaged no-console EXE.
assert "os.startfile(url)" in launcher
assert "webbrowser.open(url, new=2, autoraise=True)" in launcher

print("HomeServer single-instance and launch visibility test passed")

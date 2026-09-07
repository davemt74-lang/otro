from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
launcher = (ROOT_DIR / "desktop" / "launcher.py").read_text(encoding="utf-8")
windows_integration = (ROOT_DIR / "app" / "services" / "windows_integration.py").read_text(encoding="utf-8")
installer = (ROOT_DIR / "installer" / "HomeServer.iss").read_text(encoding="utf-8")

# Explicit user launches must open a visible HomeServer workspace after the
# loopback server is healthy instead of leaving only an easy-to-miss tray icon.
assert "def _schedule_initial_open(self) -> None:" in launcher
assert "threading.Timer(0.6, self.open_control_center).start()" in launcher
assert "threading.Timer(0.6, self.open_system).start()" in launcher
assert "self._schedule_initial_open()" in launcher

# A second explicit launch must act as Open HomeServer when the named mutex says
# the service is already running. Background/headless process contracts retain
# the existing EXIT_ALREADY_RUNNING behavior.
assert 'if "--headless" in sys.argv or "--background" in sys.argv:' in launcher
assert "health = _wait_until_listening()" in launcher
assert "_open(_path_for_health(health))" in launcher

# Windows sign-in startup remains tray/background-only so fixing manual launch
# does not create an unwanted browser tab every time the user signs in.
assert 'return f"{_quoted_executable()} --background"' in windows_integration
assert "winreg.REG_SZ, _startup_command()" in windows_integration
assert 'ValueData: """{app}\\{#MyAppExeName}"" --background"' in installer

# Prefer the Windows shell for URL activation; retain webbrowser as a fallback.
assert "os.startfile(url)" in launcher
assert "webbrowser.open(url, new=2, autoraise=True)" in launcher

print("HomeServer Windows launcher visibility contract passed")

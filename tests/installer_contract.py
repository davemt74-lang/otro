from __future__ import annotations

import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
text = (ROOT_DIR / "installer" / "HomeServer.iss").read_text(encoding="utf-8")

assert '#define MyAppVersion "0.12.0"' in text
assert 'AppId={{F94F980E-7B18-4FA3-A9B8-75A2EDE04777}' in text
assert 'DefaultDirName={localappdata}\\Programs\\HomeServer' in text
assert 'UsePreviousTasks=yes' in text
assert 'Software\\Microsoft\\Windows\\CurrentVersion\\Run' in text
assert 'ValueName: "HomeServer"' in text
assert '{userstartup}' not in text
assert re.search(r'\[UninstallRun\][\s\S]*reg delete HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', text)
assert 'HomeServer\\Data' not in text, "Installer must never package or delete the user's private data directory"

print("HomeServer installer upgrade contract test passed")

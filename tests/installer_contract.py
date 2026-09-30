from __future__ import annotations

import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
text = (ROOT_DIR / "installer" / "HomeServer.iss").read_text(encoding="utf-8")

assert '#define MyAppVersion "2.4"' in text
assert 'AppId={{F94F980E-7B18-4FA3-A9B8-75A2EDE04777}' in text
assert 'DefaultDirName={localappdata}\\Programs\\HomeServer' in text
assert 'UsePreviousTasks=yes' in text
assert 'Software\\Microsoft\\Windows\\CurrentVersion\\Run' in text
assert 'ValueName: "HomeServer"' in text
assert '{userstartup}' not in text
assert re.search(r'\[UninstallRun\][\s\S]*reg delete HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', text)
assert 'HomeServer\\Data' not in text, "Installer must never package or delete the user's private data directory"
assert 'dist\\tools\\ffmpeg\\*' in text
assert '{app}\\tools\\ffmpeg' in text
stage = (ROOT_DIR / "scripts" / "stage_ffmpeg_windows.ps1").read_text(encoding="utf-8")
assert 'ffmpeg.exe' in stage and 'ffprobe.exe' in stage
assert 'manifest.json' in stage
assert 'Get-FileHash' in stage
assert 'checksum mismatch' in stage
assert '.sha256' in stage
assert 'THIRD_PARTY_NOTICE.txt' in stage
assert '946fcce07b' in stage
assert 'GPLv3' in stage
assert 'FFMPEG_BUILD_README.txt' in stage
assert '9.0.2' in stage

print("HomeServer installer upgrade contract test passed")

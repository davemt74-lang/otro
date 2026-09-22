from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from app.config import settings


_PENDING_FORMAT = "vp3-os-pending-update-v1"


def _updates_root() -> Path:
    return (settings.runtime_dir / "updates").resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _read_pending() -> dict | None:
    path = _updates_root() / "pending-update.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def spawn_pending_update() -> bool:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False

    payload = _read_pending()
    if not payload or payload.get("format") != _PENDING_FORMAT:
        return False

    root = _updates_root()
    installer = Path(str(payload.get("installer_path") or "")).resolve()
    rollback = Path(str(payload.get("rollback_exe") or "")).resolve()
    target = Path(str(payload.get("target_exe") or "")).resolve()
    install_dir = Path(str(payload.get("install_dir") or "")).resolve()
    expected_sha = str(payload.get("installer_sha256") or "").lower()

    if not _is_within(installer, root) or not _is_within(rollback, root):
        return False
    if target != Path(sys.executable).resolve() or install_dir != target.parent:
        return False
    if not installer.is_file() or not rollback.is_file():
        return False
    if len(expected_sha) != 64 or _sha256(installer) != expected_sha:
        return False

    apply_dir = installer.parent
    result_path = apply_dir / "update-result.json"
    script_path = apply_dir / "apply-update.ps1"
    script = r'''
param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$TargetExe,
    [Parameter(Mandatory=$true)][string]$RollbackExe,
    [Parameter(Mandatory=$true)][string]$ResultPath
)

$ErrorActionPreference = 'Stop'

function Write-UpdateResult([string]$status, [string]$reason) {
    $payload = @{
        status = $status
        reason = $reason
        completed_at = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($ResultPath, $payload, [System.Text.UTF8Encoding]::new($false))
}

function Stop-TargetProcess {
    try {
        Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -eq [System.IO.Path]::GetFullPath($TargetExe))
        } | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}

Start-Sleep -Milliseconds 900

$installerArgs = @(
    '/VERYSILENT',
    '/SUPPRESSMSGBOXES',
    '/NORESTART',
    "/DIR=$InstallDir",
    '/TASKS=""'
)

try {
    $install = Start-Process -FilePath $Installer -ArgumentList $installerArgs -Wait -PassThru
    if ($install.ExitCode -ne 0) {
        throw "installer_exit_$($install.ExitCode)"
    }
} catch {
    try {
        Copy-Item -LiteralPath $RollbackExe -Destination $TargetExe -Force
        Write-UpdateResult 'rolled_back' "installer_failed:$($_.Exception.Message)"
        Start-Process -FilePath $TargetExe | Out-Null
    } catch {
        Write-UpdateResult 'failed' "installer_and_binary_rollback_failed:$($_.Exception.Message)"
    }
    exit 1
}

try {
    Start-Process -FilePath $TargetExe | Out-Null
} catch {
    try {
        Copy-Item -LiteralPath $RollbackExe -Destination $TargetExe -Force
        Write-UpdateResult 'rolled_back' 'new_binary_launch_failed'
        Start-Process -FilePath $TargetExe | Out-Null
    } catch {
        Write-UpdateResult 'failed' 'new_binary_and_binary_rollback_launch_failed'
    }
    exit 2
}

$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:4377/api/v1/health' -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {}
}

if ($healthy) {
    Write-UpdateResult 'applied' 'health_check_passed'
    exit 0
}

Stop-TargetProcess
Start-Sleep -Milliseconds 700
try {
    Copy-Item -LiteralPath $RollbackExe -Destination $TargetExe -Force
    Start-Process -FilePath $TargetExe | Out-Null
    Write-UpdateResult 'rolled_back' 'new_binary_health_check_failed'
    exit 3
} catch {
    Write-UpdateResult 'failed' "health_check_and_binary_rollback_failed:$($_.Exception.Message)"
    exit 4
}
'''.strip()
    script_path.write_text(script, encoding="utf-8")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-Installer",
                str(installer),
                "-InstallDir",
                str(install_dir),
                "-TargetExe",
                str(target),
                "-RollbackExe",
                str(rollback),
                "-ResultPath",
                str(result_path),
            ],
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError:
        return False
    return True

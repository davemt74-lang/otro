from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import stat
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from ..config import settings
from ..database import db, migration_files
from . import backups, hardware_adapters, release_readiness, system_state, vp3_os
from .runtime_control import request_runtime_command, runtime_control_available

ROLLOUT_VERSION = "v1.1"
PACKAGE_FORMAT = "vp3-os-release-v1"
MAX_PACKAGE_BYTES = 700 * 1024 * 1024
MAX_PACKAGE_UNCOMPRESSED_BYTES = 1200 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 12
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^v\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9._-]+)?$")
CHANNELS = {"stable", "beta", "dev"}
RINGS = {"pilot", "staged", "broad"}

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_ALLOWED_PACKAGE_FILES = {
    "RELEASE.json",
    "HomeServer.exe",
    "HomeServerSetup.exe",
    "SHA256SUMS.txt",
}


class RolloutError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _read_json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _version_key(value: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    match = re.match(r"^v(\d+)\.(\d+)(?:\.(\d+))?", text)
    if match is None:
        raise RolloutError("Release version is invalid.")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def _event(event_type: str, summary: str, *, severity: str = "info", metadata: dict[str, Any] | None = None) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO vp3_rollout_events(event_type,severity,summary,metadata_json)
            VALUES (?,?,?,?)
            """,
            (
                str(event_type)[:80],
                severity if severity in {"info", "warning", "error"} else "info",
                str(summary)[:240],
                _json(metadata or {}),
            ),
        )


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT release_channel,rollout_ring,automatic_apply,
                   watchdog_enabled,max_failed_starts,updated_at
            FROM vp3_rollout_settings WHERE id=1
            """
        ).fetchone()
    if row is None:
        raise RolloutError("VP3 rollout settings are unavailable.", 503)
    return {
        "release_channel": row["release_channel"],
        "rollout_ring": row["rollout_ring"],
        "automatic_apply": bool(row["automatic_apply"]),
        "watchdog_enabled": bool(row["watchdog_enabled"]),
        "max_failed_starts": int(row["max_failed_starts"]),
        "updated_at": row["updated_at"],
    }


def update_settings(
    *,
    release_channel: str | None = None,
    rollout_ring: str | None = None,
    watchdog_enabled: bool | None = None,
    max_failed_starts: int | None = None,
) -> dict[str, Any]:
    current = get_settings()
    channel = str(release_channel or current["release_channel"]).strip().lower()
    ring = str(rollout_ring or current["rollout_ring"]).strip().lower()
    if channel not in CHANNELS:
        raise RolloutError("Release channel must be stable, beta, or dev.")
    if ring not in RINGS:
        raise RolloutError("Rollout ring must be pilot, staged, or broad.")
    failed_starts = (
        int(max_failed_starts)
        if max_failed_starts is not None
        else int(current["max_failed_starts"])
    )
    if failed_starts < 1 or failed_starts > 10:
        raise RolloutError("Maximum failed starts must be between 1 and 10.")
    watchdog = (
        bool(watchdog_enabled)
        if watchdog_enabled is not None
        else bool(current["watchdog_enabled"])
    )
    with db() as connection:
        connection.execute(
            """
            UPDATE vp3_rollout_settings
            SET release_channel=?,rollout_ring=?,watchdog_enabled=?,
                max_failed_starts=?,automatic_apply=0,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (channel, ring, int(watchdog), failed_starts),
        )
    _event(
        "rollout.settings_updated",
        "VP3 rollout settings updated.",
        metadata={"release_channel": channel, "rollout_ring": ring, "watchdog_enabled": watchdog},
    )
    return get_settings()


def _hardware_assessment() -> dict[str, Any]:
    profile = vp3_os.profile_definition()
    inventory = vp3_os.hardware_inventory()
    adapter = hardware_adapters.manager.status()
    expected = list(profile.get("expected_hardware") or [])
    missing = [key for key in expected if not inventory.get(key, {}).get("present")]
    not_ready = [
        key
        for key in expected
        if inventory.get(key, {}).get("present") and not inventory.get(key, {}).get("ready")
    ]
    privacy = vp3_os.manifest(
        include_hardware=False,
        include_device_id=False,
    )["privacy"]
    privacy_fault = bool(
        privacy.get("privacy_switch_engaged")
        and not privacy.get("physical_microphone_disconnect_verified")
    )
    if privacy_fault:
        result = "failed"
    elif missing or not_ready:
        result = "degraded"
    else:
        result = "passed"
    return {
        "profile": profile,
        "inventory": inventory,
        "adapter": {
            "version": adapter.get("version"),
            "mode": adapter.get("mode"),
            "state": adapter.get("state"),
            "connected": bool(adapter.get("connected")),
            "controller": adapter.get("controller"),
            "last_error": adapter.get("last_error") or None,
            "last_seen_at": adapter.get("last_seen_at"),
        },
        "expected_hardware": expected,
        "missing_hardware": missing,
        "not_ready_hardware": not_ready,
        "privacy": privacy,
        "privacy_fault": privacy_fault,
        "result": result,
    }


def certify_hardware() -> dict[str, Any]:
    assessment = _hardware_assessment()
    profile = assessment["profile"]
    controller = assessment["adapter"].get("controller") or {}
    report = {
        **assessment,
        "vp3_os_version": vp3_os.VP3_OS_VERSION,
        "certified_at": _iso_now(),
    }
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vp3_hardware_certifications(
                profile_key,os_version,hardware_revision,firmware_version,
                controller_id,result,report_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                profile["key"],
                vp3_os.VP3_OS_VERSION,
                str(controller.get("hardware_revision") or os.environ.get("VP3_OS_HARDWARE_REVISION") or "")[:64],
                str(controller.get("firmware") or os.environ.get("VP3_OS_FIRMWARE_VERSION") or "")[:64],
                str(controller.get("controller_id") or "")[:80],
                assessment["result"],
                _json(report),
            ),
        )
        certification_id = int(cursor.lastrowid)
    severity = "error" if assessment["result"] == "failed" else (
        "warning" if assessment["result"] == "degraded" else "info"
    )
    _event(
        "hardware.certified",
        f"Hardware certification {assessment['result']}.",
        severity=severity,
        metadata={
            "certification_id": certification_id,
            "profile": profile["key"],
            "missing_hardware": assessment["missing_hardware"],
            "not_ready_hardware": assessment["not_ready_hardware"],
        },
    )
    return {"id": certification_id, **report}


def list_certifications(limit: int = 20) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,profile_key,os_version,hardware_revision,firmware_version,
                   controller_id,result,report_json,certified_at
            FROM vp3_hardware_certifications
            ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "profile_key": row["profile_key"],
            "os_version": row["os_version"],
            "hardware_revision": row["hardware_revision"] or None,
            "firmware_version": row["firmware_version"] or None,
            "controller_id": row["controller_id"] or None,
            "result": row["result"],
            "report": _read_json(row["report_json"], {}),
            "certified_at": row["certified_at"],
        }
        for row in rows
    ]


def commissioning_report() -> dict[str, Any]:
    hardware = _hardware_assessment()
    readiness = release_readiness.report()
    diagnostics = system_state.diagnostics()
    first_run = system_state.first_run_status()
    blockers: list[str] = []
    warnings: list[str] = []

    if readiness.get("production_ready") is not True:
        blockers.append("release_readiness")
    if hardware["result"] == "failed":
        blockers.append("hardware_privacy_or_safety")
    elif hardware["result"] == "degraded":
        warnings.append("hardware_degraded")
    if diagnostics.get("database", {}).get("ok") is not True:
        blockers.append("database")
    if diagnostics.get("owner_security", {}).get("exists") is not True:
        blockers.append("owner_security")
    if not first_run.get("steps", {}).get("backup_created"):
        warnings.append("no_backup")

    state = "blocked" if blockers else ("degraded" if warnings else "ready")
    return {
        "version": ROLLOUT_VERSION,
        "vp3_os_version": vp3_os.VP3_OS_VERSION,
        "state": state,
        "commissionable": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "profile": hardware["profile"],
        "hardware": hardware,
        "release_readiness": {
            "status": readiness.get("status"),
            "production_ready": readiness.get("production_ready"),
        },
        "system": {
            "database_ok": diagnostics.get("database", {}).get("ok"),
            "schema_version": diagnostics.get("database", {}).get("schema_version"),
            "owner_security": diagnostics.get("owner_security", {}).get("protection"),
            "runtime_control_available": diagnostics.get("runtime_control", {}).get("available"),
        },
        "first_run": first_run,
        "rollout": get_settings(),
    }


def _updates_root() -> Path:
    path = settings.runtime_dir / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _support_root() -> Path:
    path = settings.runtime_dir / "support"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_member(name: str) -> str:
    if not name or "\\" in name:
        raise RolloutError("Release package contains an unsafe path.")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RolloutError("Release package contains an unsafe path.")
    if len(pure.parts) != 1:
        raise RolloutError("Release package files must be at the archive root.")
    normalized = pure.as_posix()
    if normalized not in _ALLOWED_PACKAGE_FILES:
        raise RolloutError(f"Release package contains unsupported file: {normalized}")
    return normalized


def _stream_package(source: BinaryIO, target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    written = 0
    with target.open("wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_PACKAGE_BYTES:
                raise RolloutError("Release package exceeds the size limit.", 413)
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return written, digest.hexdigest()


def _validate_package(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_PACKAGE_ENTRIES:
                raise RolloutError("Release package file count is invalid.")
            actual: dict[str, zipfile.ZipInfo] = {}
            total = 0
            for info in infos:
                if info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise RolloutError("Release package cannot contain symbolic links.")
                name = _safe_member(info.filename)
                if name in actual:
                    raise RolloutError("Release package contains duplicate files.")
                total += int(info.file_size)
                if total > MAX_PACKAGE_UNCOMPRESSED_BYTES:
                    raise RolloutError("Release package expands beyond the safety limit.")
                actual[name] = info

            required = {"RELEASE.json", "HomeServerSetup.exe", "SHA256SUMS.txt"}
            if not required.issubset(actual):
                raise RolloutError("Release package is missing required production files.")
            if actual["RELEASE.json"].file_size > 64 * 1024:
                raise RolloutError("Release manifest is unexpectedly large.")

            try:
                manifest = json.loads(archive.read(actual["RELEASE.json"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RolloutError("Release manifest is invalid.") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != PACKAGE_FORMAT:
                raise RolloutError("Release package format is unsupported.")
            version = str(manifest.get("version") or "").strip()
            channel = str(manifest.get("channel") or "").strip().lower()
            if not VERSION_RE.fullmatch(version):
                raise RolloutError("Release version is invalid.")
            if channel not in CHANNELS:
                raise RolloutError("Release package channel is invalid.")
            try:
                min_schema = int(manifest.get("minimum_schema_version"))
            except (TypeError, ValueError) as exc:
                raise RolloutError("Release minimum schema version is invalid.") from exc
            supported_schema = max([1, *(v for v, _ in migration_files())])
            if min_schema > supported_schema:
                raise RolloutError(
                    "Release package requires a database schema newer than this runtime supports."
                )

            files = manifest.get("files")
            if not isinstance(files, dict):
                raise RolloutError("Release manifest file inventory is invalid.")
            verified: dict[str, str] = {}
            for name in ("HomeServerSetup.exe", "HomeServer.exe"):
                if name not in actual:
                    if name == "HomeServer.exe":
                        continue
                    raise RolloutError("Release package is missing HomeServerSetup.exe.")
                expected = str(files.get(name) or "").strip().lower()
                if not SHA256_RE.fullmatch(expected):
                    raise RolloutError(f"Release manifest is missing a valid SHA-256 for {name}.")
                digest = hashlib.sha256()
                with archive.open(actual[name], "r") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                actual_hash = digest.hexdigest()
                if actual_hash != expected:
                    raise RolloutError(f"{name} failed SHA-256 validation.")
                verified[name] = actual_hash

            sums = archive.read(actual["SHA256SUMS.txt"]).decode("ascii", errors="strict")
            for name, digest in verified.items():
                if f"{digest}  {name}" not in sums:
                    raise RolloutError(f"SHA256SUMS.txt does not match {name}.")

            return {
                "version": version,
                "channel": channel,
                "minimum_schema_version": min_schema,
                "files": verified,
                "release_notes": str(manifest.get("release_notes") or "")[:2000],
            }
    except RolloutError:
        raise
    except (zipfile.BadZipFile, KeyError, UnicodeError, OSError) as exc:
        raise RolloutError("Release package is not a valid VP3 OS update ZIP.") from exc


def stage_package(source: BinaryIO, original_name: str) -> dict[str, Any]:
    root = _updates_root()
    temporary = root / f".upload-{os.urandom(8).hex()}.zip"
    try:
        size_bytes, package_sha256 = _stream_package(source, temporary)
        metadata = _validate_package(temporary)
        current = get_settings()
        if metadata["channel"] != current["release_channel"]:
            raise RolloutError(
                f"Package channel {metadata['channel']} does not match configured "
                f"{current['release_channel']} channel."
            )
        if _version_key(metadata["version"]) < _version_key(vp3_os.VP3_OS_VERSION):
            raise RolloutError(
                "Release package is older than the installed VP3 OS. "
                "Use the controlled rollback path instead of staging a downgrade."
            )
        with db() as connection:
            existing = connection.execute(
                """
                SELECT id FROM vp3_rollout_packages
                WHERE package_sha256=?
                  AND status IN ('staged','approved','applying')
                ORDER BY id DESC LIMIT 1
                """,
                (package_sha256,),
            ).fetchone()
        if existing is not None:
            return get_package(int(existing["id"]))
        final = root / f"{package_sha256}.zip"
        if not final.exists():
            os.replace(temporary, final)
        else:
            temporary.unlink(missing_ok=True)

        with db() as connection:
            cursor = connection.execute(
                """
                INSERT INTO vp3_rollout_packages(
                    version,channel,package_sha256,installer_sha256,
                    package_name,status,metadata_json
                ) VALUES (?,?,?,?,?,'staged',?)
                """,
                (
                    metadata["version"],
                    metadata["channel"],
                    package_sha256,
                    metadata["files"]["HomeServerSetup.exe"],
                    Path(str(original_name or "vp3-os-update.zip")).name[:240],
                    _json({**metadata, "size_bytes": size_bytes}),
                ),
            )
            package_id = int(cursor.lastrowid)
        _event(
            "update.staged",
            f"VP3 OS {metadata['version']} update package staged.",
            metadata={"package_id": package_id, "channel": metadata["channel"]},
        )
        return get_package(package_id)
    finally:
        temporary.unlink(missing_ok=True)


def _package_row(package_id: int):
    with db() as connection:
        return connection.execute(
            """
            SELECT id,version,channel,package_sha256,installer_sha256,
                   package_name,status,rollback_backup_name,failure_reason,
                   metadata_json,staged_at,approved_at,applied_at,updated_at
            FROM vp3_rollout_packages WHERE id=?
            """,
            (int(package_id),),
        ).fetchone()


def get_package(package_id: int) -> dict[str, Any]:
    row = _package_row(package_id)
    if row is None:
        raise RolloutError("Rollout package was not found.", 404)
    return {
        "id": int(row["id"]),
        "version": row["version"],
        "channel": row["channel"],
        "package_sha256": row["package_sha256"],
        "installer_sha256": row["installer_sha256"],
        "package_name": row["package_name"],
        "status": row["status"],
        "rollback_backup_name": row["rollback_backup_name"] or None,
        "failure_reason": row["failure_reason"] or None,
        "metadata": _read_json(row["metadata_json"], {}),
        "staged_at": row["staged_at"],
        "approved_at": row["approved_at"],
        "applied_at": row["applied_at"],
        "updated_at": row["updated_at"],
    }


def list_packages(limit: int = 20) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM vp3_rollout_packages ORDER BY id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        ]
    return [get_package(package_id) for package_id in ids]


def approve_package(package_id: int) -> dict[str, Any]:
    package = get_package(package_id)
    if package["status"] != "staged":
        raise RolloutError("Only a staged update can be approved.", 409)
    backup = backups.create_backup(f"pre-update-{package['version']}")
    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE vp3_rollout_packages
            SET status='approved',rollback_backup_name=?,approved_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='staged'
            """,
            (backup["name"], int(package_id)),
        )
        if cursor.rowcount != 1:
            raise RolloutError("Update approval state changed; refresh and try again.", 409)
    _event(
        "update.approved",
        f"VP3 OS {package['version']} update approved with rollback backup.",
        metadata={"package_id": int(package_id), "rollback_backup_name": backup["name"]},
    )
    return get_package(package_id)



def _package_archive_path(package: dict[str, Any]) -> Path:
    path = _updates_root() / f"{package['package_sha256']}.zip"
    if not path.is_file():
        raise RolloutError("The staged release package is no longer available.", 410)
    return path


def _pending_update_path() -> Path:
    return _updates_root() / "pending-update.json"


def request_apply(package_id: int) -> dict[str, Any]:
    package = get_package(package_id)
    if package["status"] != "approved":
        raise RolloutError("Only an approved update can be applied.", 409)
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RolloutError(
            "Applying an update requires the installed Windows HomeServer application.",
            422,
        )
    if not runtime_control_available():
        raise RolloutError("Runtime update control is unavailable in this launch mode.", 503)

    archive_path = _package_archive_path(package)
    apply_dir = _updates_root() / f"apply-{int(package_id)}"
    if apply_dir.exists():
        shutil.rmtree(apply_dir, ignore_errors=True)
    apply_dir.mkdir(parents=True, exist_ok=True)
    installer_path = apply_dir / "HomeServerSetup.exe"
    rollback_exe = apply_dir / "HomeServer.rollback.exe"

    with zipfile.ZipFile(archive_path, "r") as archive:
        with archive.open("HomeServerSetup.exe", "r") as source, installer_path.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    installer_hash = hashlib.sha256(installer_path.read_bytes()).hexdigest()
    if installer_hash != package["installer_sha256"]:
        shutil.rmtree(apply_dir, ignore_errors=True)
        raise RolloutError("Staged installer failed revalidation before apply.")

    current_exe = Path(sys.executable).resolve()
    try:
        shutil.copy2(current_exe, rollback_exe)
    except OSError as exc:
        shutil.rmtree(apply_dir, ignore_errors=True)
        raise RolloutError("Could not create the binary rollback point.") from exc

    pending = {
        "format": "vp3-os-pending-update-v1",
        "package_id": int(package_id),
        "version": package["version"],
        "installer_path": str(installer_path),
        "installer_sha256": package["installer_sha256"],
        "rollback_exe": str(rollback_exe),
        "target_exe": str(current_exe),
        "install_dir": str(current_exe.parent),
        "rollback_backup_name": package["rollback_backup_name"],
        "requested_at": _iso_now(),
    }
    pending_path = _pending_update_path()
    temporary = pending_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(pending, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, pending_path)

    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE vp3_rollout_packages
            SET status='applying',updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='approved'
            """,
            (int(package_id),),
        )
        if cursor.rowcount != 1:
            pending_path.unlink(missing_ok=True)
            shutil.rmtree(apply_dir, ignore_errors=True)
            raise RolloutError("Update apply state changed; refresh and try again.", 409)
    _event(
        "update.apply_requested",
        f"VP3 OS {package['version']} controlled update apply requested.",
        metadata={"package_id": int(package_id)},
    )
    if not request_runtime_command("apply_update"):
        pending_path.unlink(missing_ok=True)
        with db() as connection:
            connection.execute(
                """
                UPDATE vp3_rollout_packages
                SET status='approved',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='applying'
                """,
                (int(package_id),),
            )
        raise RolloutError("Runtime rejected the controlled update request.", 503)
    return {
        **get_package(package_id),
        "shutdown_requested": True,
        "binary_rollback_ready": True,
        "data_rollback_backup_ready": bool(package["rollback_backup_name"]),
    }


def pending_update_status() -> dict[str, Any] | None:
    path = _pending_update_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"valid": False}
    if not isinstance(payload, dict):
        return {"valid": False}
    return {
        "valid": payload.get("format") == "vp3-os-pending-update-v1",
        "package_id": payload.get("package_id"),
        "version": payload.get("version"),
        "requested_at": payload.get("requested_at"),
        "rollback_backup_name": payload.get("rollback_backup_name"),
    }


def discard_package(package_id: int) -> dict[str, Any]:
    package = get_package(package_id)
    if package["status"] in {"applying", "applied"}:
        raise RolloutError("An applying or applied update cannot be discarded.", 409)
    with db() as connection:
        connection.execute(
            """
            UPDATE vp3_rollout_packages
            SET status='discarded',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(package_id),),
        )
    _event(
        "update.discarded",
        f"VP3 OS {package['version']} staged update discarded.",
        metadata={"package_id": int(package_id)},
    )
    return get_package(package_id)



def reconcile_update_results() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,status FROM vp3_rollout_packages
            WHERE status='applying'
            ORDER BY id
            """
        ).fetchall()

    reconciled: list[dict[str, Any]] = []
    for row in rows:
        package_id = int(row["id"])
        result_path = _updates_root() / f"apply-{package_id}" / "update-result.json"
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "").strip().lower()
        reason = str(result.get("reason") or "")[:500]
        if status not in {"applied", "rolled_back", "failed"}:
            continue

        with db() as connection:
            connection.execute(
                """
                UPDATE vp3_rollout_packages
                SET status=?,failure_reason=?,
                    applied_at=CASE WHEN ?='applied' THEN CURRENT_TIMESTAMP ELSE applied_at END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='applying'
                """,
                (
                    status,
                    reason if status != "applied" else None,
                    status,
                    package_id,
                ),
            )
        _pending_update_path().unlink(missing_ok=True)
        _event(
            f"update.{status}",
            f"Controlled update {status.replace('_', ' ')}.",
            severity="error" if status == "failed" else (
                "warning" if status == "rolled_back" else "info"
            ),
            metadata={"package_id": package_id, "reason": reason},
        )
        reconciled.append(get_package(package_id))
    return reconciled



def support_bundle() -> dict[str, Any]:
    root = _support_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = root / f"vp3-support-{stamp}.zip"

    diagnostics = system_state.diagnostics()
    safe_diagnostics = {
        "version": diagnostics.get("version"),
        "database": diagnostics.get("database"),
        "backups": {
            "count": diagnostics.get("backups", {}).get("count"),
            "pending_restore": bool(
                (diagnostics.get("backups", {}).get("pending_restore") or {}).get("valid")
            ),
            "error": diagnostics.get("backups", {}).get("error"),
        },
        "startup": {
            "supported": diagnostics.get("startup", {}).get("supported"),
            "enabled": diagnostics.get("startup", {}).get("enabled"),
            "registry_enabled": diagnostics.get("startup", {}).get("registry_enabled"),
            "legacy_shortcut": diagnostics.get("startup", {}).get("legacy_shortcut"),
            "mode": diagnostics.get("startup", {}).get("mode"),
        },
        "owner_security": {
            "protection": diagnostics.get("owner_security", {}).get("protection"),
            "exists": diagnostics.get("owner_security", {}).get("exists"),
            "recovered_corrupt_secret": diagnostics.get("owner_security", {}).get(
                "recovered_corrupt_secret"
            ),
        },
        "runtime_control": diagnostics.get("runtime_control"),
    }
    commissioning = commissioning_report()
    commissioning["hardware"]["adapter"]["controller"] = {
        key: value
        for key, value in (commissioning["hardware"]["adapter"].get("controller") or {}).items()
        if key in {"protocol", "firmware", "hardware_revision", "components", "capabilities"}
    }

    with db() as connection:
        events = [
            {
                "event_type": row["event_type"],
                "severity": row["severity"],
                "summary": row["summary"],
                "metadata": _read_json(row["metadata_json"], {}),
                "created_at": row["created_at"],
            }
            for row in connection.execute(
                """
                SELECT event_type,severity,summary,metadata_json,created_at
                FROM vp3_rollout_events ORDER BY id DESC LIMIT 100
                """
            ).fetchall()
        ]

    payloads = {
        "support-summary.json": {
            "format": "vp3-os-support-v1",
            "created_at": _iso_now(),
            "vp3_os_version": vp3_os.VP3_OS_VERSION,
            "rollout_version": ROLLOUT_VERSION,
            "privacy": {
                "conversations_included": False,
                "recordings_included": False,
                "knowledge_content_included": False,
                "credentials_included": False,
                "absolute_paths_included": False,
            },
        },
        "diagnostics.json": safe_diagnostics,
        "commissioning.json": commissioning,
        "rollout-events.json": events,
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(
                name,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            )
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    _event(
        "support.bundle_created",
        "Sanitized VP3 OS support bundle created.",
        metadata={"sha256": digest},
    )
    return {
        "path": target,
        "name": target.name,
        "sha256": digest,
        "size_bytes": target.stat().st_size,
    }


def list_events(limit: int = 50) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 200))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,event_type,severity,summary,metadata_json,created_at
            FROM vp3_rollout_events ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "event_type": row["event_type"],
            "severity": row["severity"],
            "summary": row["summary"],
            "metadata": _read_json(row["metadata_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def overview() -> dict[str, Any]:
    reconcile_update_results()
    certifications = list_certifications(5)
    packages = list_packages(10)
    return {
        "version": ROLLOUT_VERSION,
        "vp3_os_version": vp3_os.VP3_OS_VERSION,
        "settings": get_settings(),
        "commissioning": commissioning_report(),
        "latest_certification": certifications[0] if certifications else None,
        "certifications": certifications,
        "packages": packages,
        "pending_update": pending_update_status(),
        "events": list_events(30),
        "governance": {
            "automatic_apply": False,
            "owner_staging_required": True,
            "owner_approval_required": True,
            "pre_update_backup_required": True,
            "remote_unattended_updates": False,
            "support_bundle_private_content_included": False,
        },
    }



def _reconcile_loop() -> None:
    while not _STOP.wait(2.0):
        try:
            reconcile_update_results()
        except Exception:
            # Update-state reconciliation must never take down HomeServer.
            pass


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(
        target=_reconcile_loop,
        name="vp3-rollout-reconcile",
        daemon=True,
    )
    _THREAD.start()


def stop() -> None:
    global _THREAD
    _STOP.set()
    thread = _THREAD
    if thread and thread.is_alive():
        thread.join(timeout=3)
    _THREAD = None



def public_capability() -> dict[str, Any]:
    return {
        "version": ROLLOUT_VERSION,
        "hardware_certification": True,
        "commissioning": True,
        "release_channels": sorted(CHANNELS),
        "rollout_rings": sorted(RINGS),
        "staged_updates": True,
        "pre_update_backup_required": True,
        "automatic_apply": False,
        "sanitized_support_bundle": True,
        "remote_unattended_updates": False,
    }

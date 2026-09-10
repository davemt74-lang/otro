from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import settings
from ..database import db
from .local_app_catalog import CATALOG, CATALOG_VERSION

LOCAL_APPS_VERSION = "v0.40"
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 90
_ERROR_LIMIT = 1200

# Source hosts are fixed by the embedded HomeServer catalog. Redirects are
# independently checked so a trusted URL cannot bounce the installer to an
# arbitrary host.
_TRUSTED_HOSTS = {
    "github.com",
    "huggingface.co",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}
_TRUSTED_HOST_SUFFIXES = (
    ".xethub.hf.co",
    ".hf.co",
    ".githubusercontent.com",
)

_LOCK_GUARD = threading.Lock()
_APP_LOCKS: dict[str, threading.Lock] = {}


class LocalAppError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _assert_trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _lock_for(app_key: str) -> threading.Lock:
    with _LOCK_GUARD:
        return _APP_LOCKS.setdefault(app_key, threading.Lock())


def _apps_root() -> Path:
    root = settings.data_dir / "local-apps"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".staging").mkdir(parents=True, exist_ok=True)
    (root / ".rollback").mkdir(parents=True, exist_ok=True)
    return root


def _assert_trusted_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise LocalAppError("Local App downloads require HTTPS from a trusted catalog host.", 502)
    allowed = host in _TRUSTED_HOSTS or any(host.endswith(suffix) for suffix in _TRUSTED_HOST_SUFFIXES)
    if not allowed:
        raise LocalAppError(f"Local App download redirected to an untrusted host: {host}", 502)
    if parsed.username or parsed.password:
        raise LocalAppError("Credential-bearing Local App download URLs are not allowed.", 502)


def _safe_rel_path(value: str) -> PurePosixPath:
    rel = PurePosixPath(str(value or "").replace("\\", "/"))
    if not rel.parts or rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise LocalAppError("Catalog contains an unsafe package path.", 500)
    if ":" in rel.parts[0]:
        raise LocalAppError("Catalog contains an unsafe package path.", 500)
    return rel


def _under(root: Path, rel: str) -> Path:
    safe = _safe_rel_path(rel)
    target = root.joinpath(*safe.parts)
    resolved_root = root.resolve()
    resolved_parent = target.parent.resolve()
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise LocalAppError("Catalog package path escaped the Local Apps directory.", 500)
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(artifact: dict[str, Any], destination: Path) -> dict[str, Any]:
    url = str(artifact.get("url") or "")
    expected_hash = str(artifact.get("sha256") or "").lower()
    expected_size = int(artifact.get("size_bytes") or 0)
    max_bytes = int(artifact.get("max_bytes") or max(expected_size * 2, 10 * 1024 * 1024))
    if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
        raise LocalAppError("Catalog artifact is missing a valid SHA-256 digest.", 500)
    _assert_trusted_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    opener = urllib.request.build_opener(_TrustedRedirectHandler())
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"HomeServer/{settings.version} LocalAppInstaller/{LOCAL_APPS_VERSION}",
            "Accept": "application/octet-stream,*/*;q=0.8",
        },
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with opener.open(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response, temp.open("wb") as output:
            _assert_trusted_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise LocalAppError("Local App artifact is larger than its catalog limit.", 502)
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise LocalAppError("Local App artifact exceeded its catalog size limit.", 502)
                digest.update(chunk)
                output.write(chunk)
    except LocalAppError:
        temp.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        temp.unlink(missing_ok=True)
        raise LocalAppError(f"Unable to download Local App artifact: {exc}", 502) from exc

    actual_hash = digest.hexdigest()
    if actual_hash != expected_hash:
        temp.unlink(missing_ok=True)
        raise LocalAppError("Local App artifact failed SHA-256 verification.", 502)
    if expected_size and total != expected_size:
        temp.unlink(missing_ok=True)
        raise LocalAppError("Local App artifact size does not match the trusted catalog.", 502)
    os.replace(temp, destination)
    return {"name": artifact["name"], "sha256": actual_hash, "size_bytes": total}


def _extract_zip(archive: Path, destination: Path, max_unpacked_bytes: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            for info in bundle.infolist():
                name = str(info.filename or "").replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                rel = _safe_rel_path(name)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise LocalAppError("Local App archive contains a symbolic link.", 502)
                total += int(info.file_size)
                if total > max_unpacked_bytes:
                    raise LocalAppError("Local App archive exceeds its unpacked size limit.", 502)
                target = _under(destination, str(rel))
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=_DOWNLOAD_CHUNK_BYTES)
    except zipfile.BadZipFile as exc:
        raise LocalAppError("Downloaded Local App archive is not a valid ZIP file.", 502) from exc


def _platform_snapshot() -> dict[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        machine = "amd64"
    elif machine in {"aarch64", "arm64"}:
        machine = "arm64"
    return {"os": system, "arch": machine}


def _support(package: dict[str, Any]) -> tuple[bool, str | None]:
    current = _platform_snapshot()
    requirements = package.get("requirements") or {}
    systems = [str(value).lower() for value in requirements.get("os", [])]
    arches = [str(value).lower() for value in requirements.get("arch", [])]
    if systems and current["os"] not in systems:
        return False, f"Requires {', '.join(systems)}; this HomeServer is {current['os']}."
    if arches and current["arch"] not in arches:
        return False, f"Requires {', '.join(arches)}; this HomeServer is {current['arch']}."
    return True, None


def _decode_json(raw: str | None, fallback):  # noqa: ANN001, ANN201
    try:
        value = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value


def _row_to_public(row) -> dict[str, Any] | None:  # noqa: ANN001
    if row is None:
        return None
    item = dict(row)
    manifest = _decode_json(item.pop("artifact_manifest_json", "[]"), [])
    capabilities = _decode_json(item.pop("capabilities_json", "[]"), [])
    item["capabilities"] = capabilities if isinstance(capabilities, list) else []
    item["disk_bytes"] = sum(int(entry.get("size_bytes") or 0) for entry in manifest if isinstance(entry, dict))
    # Never disclose owner filesystem paths through the API.
    item.pop("install_rel_path", None)
    return item


def _installed_row(app_key: str):  # noqa: ANN201
    with db() as connection:
        return connection.execute(
            "SELECT * FROM local_apps WHERE app_key=?",
            (app_key,),
        ).fetchone()


def _log(action: str, app_key: str, metadata: dict[str, Any] | None = None) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'local-apps', ?, 'local_app', ?, ?)
            """,
            (action, app_key, json.dumps(metadata or {}, separators=(",", ":"))),
        )


def _set_operation_state(package: dict[str, Any], status: str) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO local_apps(
                app_key, name, installed_version, catalog_version, status,
                install_rel_path, capabilities_json, artifact_manifest_json,
                source_label, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, NULL)
            ON CONFLICT(app_key) DO UPDATE SET
                name=excluded.name,
                catalog_version=excluded.catalog_version,
                status=excluded.status,
                capabilities_json=excluded.capabilities_json,
                source_label=excluded.source_label,
                updated_at=CURRENT_TIMESTAMP,
                last_error=NULL
            """,
            (
                package["key"],
                package["name"],
                package["version"],
                CATALOG_VERSION,
                status,
                package["key"],
                json.dumps(package.get("capabilities") or [], separators=(",", ":")),
                package.get("source_label"),
            ),
        )


def _restore_row(snapshot: dict[str, Any], error: str) -> None:
    with db() as connection:
        connection.execute(
            """
            UPDATE local_apps SET
                name=?, installed_version=?, catalog_version=?, status=?,
                install_rel_path=?, capabilities_json=?, artifact_manifest_json=?,
                manifest_sha256=?, source_label=?, installed_at=?,
                updated_at=CURRENT_TIMESTAMP, last_error=?
            WHERE app_key=?
            """,
            (
                snapshot["name"], snapshot["installed_version"], snapshot["catalog_version"],
                snapshot["status"], snapshot["install_rel_path"], snapshot["capabilities_json"],
                snapshot["artifact_manifest_json"], snapshot["manifest_sha256"], snapshot["source_label"],
                snapshot["installed_at"], error[:_ERROR_LIMIT], snapshot["app_key"],
            ),
        )


def _record_failure(package: dict[str, Any], error: str) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO local_apps(
                app_key, name, installed_version, catalog_version, status,
                install_rel_path, capabilities_json, artifact_manifest_json,
                source_label, last_error
            ) VALUES (?, ?, ?, ?, 'failed', ?, ?, '[]', ?, ?)
            ON CONFLICT(app_key) DO UPDATE SET
                status='failed', updated_at=CURRENT_TIMESTAMP, last_error=excluded.last_error
            """,
            (
                package["key"], package["name"], package["version"], CATALOG_VERSION,
                package["key"], json.dumps(package.get("capabilities") or [], separators=(",", ":")),
                package.get("source_label"), error[:_ERROR_LIMIT],
            ),
        )


def _record_success(package: dict[str, Any], manifest: list[dict[str, Any]]) -> None:
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO local_apps(
                app_key, name, installed_version, catalog_version, status,
                install_rel_path, capabilities_json, artifact_manifest_json,
                manifest_sha256, source_label, last_error
            ) VALUES (?, ?, ?, ?, 'installed', ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(app_key) DO UPDATE SET
                name=excluded.name,
                installed_version=excluded.installed_version,
                catalog_version=excluded.catalog_version,
                status='installed',
                install_rel_path=excluded.install_rel_path,
                capabilities_json=excluded.capabilities_json,
                artifact_manifest_json=excluded.artifact_manifest_json,
                manifest_sha256=excluded.manifest_sha256,
                source_label=excluded.source_label,
                updated_at=CURRENT_TIMESTAMP,
                last_error=NULL
            """,
            (
                package["key"], package["name"], package["version"], CATALOG_VERSION,
                package["key"], json.dumps(package.get("capabilities") or [], separators=(",", ":")),
                manifest_json, manifest_hash, package.get("source_label"),
            ),
        )


def _public_package(package: dict[str, Any], installed) -> dict[str, Any]:  # noqa: ANN001
    supported, reason = _support(package)
    installed_item = _row_to_public(installed)
    artifact_bytes = sum(int(item.get("size_bytes") or 0) for item in package.get("artifacts", []))
    return {
        "key": package["key"],
        "name": package["name"],
        "version": package["version"],
        "category": package["category"],
        "description": package["description"],
        "capabilities": list(package.get("capabilities") or []),
        "requirements": dict(package.get("requirements") or {}),
        "source_label": package.get("source_label"),
        "license": package.get("license"),
        "runtime": package.get("runtime"),
        "default_voice": package.get("default_voice"),
        "download_bytes": artifact_bytes,
        "integrity": "sha256-pinned",
        "supported": supported,
        "support_reason": reason,
        "installed": installed_item,
        "update_available": bool(installed_item and installed_item.get("installed_version") != package["version"]),
    }


def catalog() -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM local_apps").fetchall()
    installed = {row["app_key"]: row for row in rows}
    packages = [_public_package(package, installed.get(key)) for key, package in CATALOG.items()]
    return {
        "version": LOCAL_APPS_VERSION,
        "catalog_version": CATALOG_VERSION,
        "platform": _platform_snapshot(),
        "packages": packages,
        "installed_count": sum(1 for item in packages if item["installed"] and item["installed"]["status"] == "installed"),
    }


def installed_capabilities() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT app_key, name, installed_version, status, capabilities_json FROM local_apps WHERE status='installed' ORDER BY app_key"
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        capabilities = _decode_json(row["capabilities_json"], [])
        result.append({
            "key": row["app_key"],
            "name": row["name"],
            "version": row["installed_version"],
            "status": row["status"],
            "capabilities": capabilities if isinstance(capabilities, list) else [],
            "local": True,
        })
    return result


def install(app_key: str, *, update: bool = False) -> dict[str, Any]:
    package = CATALOG.get(app_key)
    if package is None:
        raise LocalAppError("Local App is not present in the trusted HomeServer catalog.", 404)
    supported, reason = _support(package)
    if not supported:
        raise LocalAppError(reason or "Local App is not supported on this system.", 409)

    lock = _lock_for(app_key)
    if not lock.acquire(blocking=False):
        raise LocalAppError("An install operation is already running for this Local App.", 409)

    root = _apps_root()
    active = root / app_key
    staging = root / ".staging" / f"{app_key}-{uuid.uuid4().hex}"
    payload = staging / "payload"
    rollback = root / ".rollback" / f"{app_key}-{uuid.uuid4().hex}"
    previous_row = _installed_row(app_key)
    previous = dict(previous_row) if previous_row is not None else None
    had_active = active.exists()
    moved_previous = False
    try:
        if previous and previous.get("status") == "installed" and previous.get("installed_version") == package["version"]:
            return {"changed": False, "reason": "already_current", "package": _public_package(package, previous_row)}
        if update and not previous:
            raise LocalAppError("Local App is not installed; use Install first.", 409)

        _set_operation_state(package, "updating" if previous else "installing")
        _log("local_app.update.started" if previous else "local_app.install.started", app_key, {"version": package["version"]})
        payload.mkdir(parents=True, exist_ok=False)
        manifest: list[dict[str, Any]] = []
        for artifact in package.get("artifacts", []):
            kind = artifact.get("kind", "file")
            if kind == "zip":
                archive = _under(staging, f"downloads/{artifact['name']}.zip")
                entry = _download(artifact, archive)
                target_dir = _under(payload, artifact["target"])
                _extract_zip(archive, target_dir, int(artifact.get("max_unpacked_bytes") or 512 * 1024 * 1024))
            elif kind == "file":
                destination = _under(payload, artifact["target"])
                entry = _download(artifact, destination)
            else:
                raise LocalAppError("Catalog contains an unsupported artifact type.", 500)
            manifest.append(entry)

        for required in package.get("required_paths", []):
            required_path = _under(payload, required)
            if not required_path.is_file():
                raise LocalAppError(f"Local App health check failed: missing {required}.", 502)

        install_manifest = {
            "catalog_version": CATALOG_VERSION,
            "app_key": app_key,
            "name": package["name"],
            "version": package["version"],
            "capabilities": package.get("capabilities") or [],
            "artifacts": manifest,
        }
        (payload / "homeserver-app.json").write_text(
            json.dumps(install_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if active.exists():
            os.replace(active, rollback)
            moved_previous = True
        os.replace(payload, active)
        _record_success(package, manifest)
        shutil.rmtree(rollback, ignore_errors=True)
        _log("local_app.updated" if previous else "local_app.installed", app_key, {"version": package["version"], "artifact_count": len(manifest)})
        return {"changed": True, "package": _public_package(package, _installed_row(app_key))}
    except LocalAppError as exc:
        if moved_previous and rollback.exists():
            shutil.rmtree(active, ignore_errors=True)
            os.replace(rollback, active)
        if previous:
            _restore_row(previous, str(exc))
        else:
            _record_failure(package, str(exc))
        _log("local_app.update.failed" if previous else "local_app.install.failed", app_key, {"error": str(exc)[:_ERROR_LIMIT]})
        raise
    except Exception as exc:  # fail closed and keep the previous installation intact
        if moved_previous and rollback.exists():
            shutil.rmtree(active, ignore_errors=True)
            os.replace(rollback, active)
        message = f"Local App installation failed safely: {exc}"
        if previous:
            _restore_row(previous, message)
        else:
            _record_failure(package, message)
        _log("local_app.update.failed" if previous else "local_app.install.failed", app_key, {"error": message[:_ERROR_LIMIT]})
        raise LocalAppError(message, 500) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not had_active and rollback.exists():
            shutil.rmtree(rollback, ignore_errors=True)
        lock.release()


def uninstall(app_key: str) -> dict[str, Any]:
    package = CATALOG.get(app_key)
    row = _installed_row(app_key)
    if row is None:
        if package is None:
            raise LocalAppError("Local App not found.", 404)
        return {"changed": False, "reason": "not_installed"}
    lock = _lock_for(app_key)
    if not lock.acquire(blocking=False):
        raise LocalAppError("An install operation is already running for this Local App.", 409)
    try:
        root = _apps_root().resolve()
        active = (root / app_key).resolve()
        if active.parent != root:
            raise LocalAppError("Refusing to remove a path outside the Local Apps directory.", 500)
        if active.exists():
            shutil.rmtree(active)
        with db() as connection:
            connection.execute("DELETE FROM local_apps WHERE app_key=?", (app_key,))
        _log("local_app.uninstalled", app_key, {"version": row["installed_version"]})
        return {"changed": True, "app_key": app_key}
    except OSError as exc:
        raise LocalAppError(f"Unable to remove Local App files: {exc}", 500) from exc
    finally:
        lock.release()

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi.responses import FileResponse, Response

from . import hosting_deployment, hosting_runtime

SERVING_CONTRACT = "vp3.hosting.serving.v1"
PHP_TIMEOUT_SECONDS = 5
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
MAX_PHP_OUTPUT_BYTES = 8 * 1024 * 1024
_ALLOWED_METHODS = {"GET", "HEAD", "POST"}
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,80}$")


class ServingError(hosting_runtime.HostingError):
    pass


def php_cgi_path() -> str | None:
    return shutil.which("php-cgi")


def _normalize_request_path(value: str) -> PurePosixPath:
    raw = str(value or "").replace("\\", "/").lstrip("/")
    if not raw:
        return PurePosixPath(".")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        raise ServingError("Requested site path is invalid.", 400)
    return path


def _resolve_target(site_id: str, request_path: str) -> Path:
    root = hosting_deployment.active_public_root(site_id).resolve()
    rel = _normalize_request_path(request_path)
    target = root if rel.as_posix() == "." else (root / Path(*rel.parts)).resolve()
    if target != root and root not in target.parents:
        raise ServingError("Requested site path escaped the active release.", 400)
    if target.exists() and target.is_symlink():
        raise ServingError("Hosted site symlinks are not served.", 403)
    if target.is_dir():
        for index_name in ("index.html", "index.htm", "index.php"):
            candidate = target / index_name
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        raise ServingError("Hosted directory has no index document.", 404)
    if not target.is_file():
        raise ServingError("Hosted resource not found.", 404)
    return target


def _safe_runtime_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _parse_cgi_output(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    if len(raw) > MAX_PHP_OUTPUT_BYTES:
        raise ServingError("PHP response exceeded the output limit.", 502)
    marker = b"\r\n\r\n"
    split = raw.find(marker)
    if split < 0:
        marker = b"\n\n"
        split = raw.find(marker)
    if split < 0:
        raise ServingError("PHP runtime returned an invalid CGI response.", 502)
    header_blob = raw[:split].decode("latin-1", "replace")
    body = raw[split + len(marker):]
    status = 200
    headers: dict[str, str] = {}
    for line in header_blob.replace("\r\n", "\n").split("\n"):
        if not line.strip() or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        value = value.strip()
        if name.lower() == "status":
            try:
                status = int(value.split(" ", 1)[0])
            except (ValueError, IndexError):
                raise ServingError("PHP runtime returned an invalid status header.", 502)
            continue
        if not _HEADER_NAME.fullmatch(name):
            continue
        lower = name.lower()
        if lower in {"connection", "transfer-encoding", "content-length", "set-cookie"}:
            continue
        headers[name] = value
    return status, headers, body


def _execute_php(
    site_id: str,
    target: Path,
    *,
    method: str,
    query_string: str,
    content_type: str | None,
    body: bytes,
    request_path: str,
) -> Response:
    binary = php_cgi_path()
    if not binary:
        raise ServingError("PHP CGI runtime is not installed on this HomeServer.", 503)
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise ServingError("Hosted request body exceeds the runtime limit.", 413)

    public_root = hosting_deployment.active_public_root(site_id).resolve()
    env = _safe_runtime_environment()
    env.update({
        "GATEWAY_INTERFACE": "CGI/1.1",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "SERVER_SOFTWARE": "VP3-HomeServer",
        "REQUEST_METHOD": method,
        "QUERY_STRING": query_string,
        "SCRIPT_FILENAME": str(target),
        "SCRIPT_NAME": "/" + str(request_path or target.name).replace("\\", "/").lstrip("/"),
        "DOCUMENT_ROOT": str(public_root),
        "REDIRECT_STATUS": "1",
        "CONTENT_LENGTH": str(len(body)),
    })
    if content_type:
        env["CONTENT_TYPE"] = content_type

    try:
        completed = subprocess.run(
            [binary],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=public_root,
            env=env,
            timeout=PHP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServingError("PHP request exceeded the execution time limit.", 504) from exc
    except OSError as exc:
        raise ServingError("PHP runtime could not be started.", 503) from exc

    if completed.returncode != 0:
        raise ServingError("PHP runtime failed while serving the request.", 502)
    status, headers, response_body = _parse_cgi_output(completed.stdout)
    media_type = headers.pop("Content-Type", headers.pop("content-type", None))
    return Response(
        content=b"" if method == "HEAD" else response_body,
        status_code=status,
        headers=headers,
        media_type=media_type,
    )


def serve(
    site_id: str,
    request_path: str,
    *,
    method: str = "GET",
    query_string: str = "",
    content_type: str | None = None,
    body: bytes = b"",
):
    site = hosting_runtime.get_site(site_id)
    method = str(method or "GET").upper()
    if method not in _ALLOWED_METHODS:
        raise ServingError("Hosted runtime method is not allowed.", 405)
    if site["state"] != "active":
        raise ServingError("Hosted site is not active.", 503)
    target = _resolve_target(site_id, request_path)

    if target.suffix.lower() == ".php":
        if str(site["runtime_kind"]).lower() != "php":
            raise ServingError("PHP execution is not enabled for this site.", 403)
        return _execute_php(
            site_id,
            target,
            method=method,
            query_string=query_string,
            content_type=content_type,
            body=body,
            request_path=request_path,
        )

    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=None,
        headers={"Cache-Control": "no-store"},
    )


def runtime_health(site_id: str) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    deployment = hosting_deployment.deployment_status(site_id)
    active = deployment.get("active_release")
    result: dict[str, Any] = {
        "contract": SERVING_CONTRACT,
        "site_id": site_id,
        "site_state": site["state"],
        "runtime": site["runtime_kind"],
        "active_release_id": deployment.get("active_release_id"),
        "deployment_ready": bool(active),
        "sqlite": hosting_runtime.database_health(site_id),
        "local_serving_ready": False,
        "php_cgi_available": bool(php_cgi_path()),
        "public_routing": False,
    }
    if not active or site["state"] != "active":
        return result
    try:
        entrypoint = str(active.get("entrypoint") or "")
        target = _resolve_target(site_id, entrypoint.removeprefix("public/"))
        runtime = str(site["runtime_kind"]).lower()
        if target.suffix.lower() == ".php" or runtime == "php":
            result["local_serving_ready"] = bool(php_cgi_path())
        else:
            result["local_serving_ready"] = target.is_file()
    except ServingError:
        result["local_serving_ready"] = False
    return result


def public_capability() -> dict[str, Any]:
    return {
        "contract": SERVING_CONTRACT,
        "loopback_serving": True,
        "owner_preview": True,
        "static_files": True,
        "php_cgi": bool(php_cgi_path()),
        "php_timeout_seconds": PHP_TIMEOUT_SECONDS,
        "max_request_body_bytes": MAX_REQUEST_BODY_BYTES,
        "max_php_output_bytes": MAX_PHP_OUTPUT_BYTES,
        "secret_environment_inheritance": False,
        "site_state_gate": True,
        "active_release_gate": True,
        "public_routing": False,
    }

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs
from typing import Any

from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from . import hosting_deployment, hosting_runtime, hosting_scheduler

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
    hosting_runtime._ensure_no_symlink(target.parent)
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


def _parse_cgi_output(raw: bytes) -> tuple[int, dict[str, str], list[str], bytes]:
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
    cookies: list[str] = []
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
        if lower in {"connection", "transfer-encoding", "content-length"}:
            continue
        if lower == "set-cookie":
            cookies.append(value)
            continue
        headers[name] = value
    return status, headers, cookies, body


def _execute_php(
    site_id: str,
    target: Path,
    *,
    method: str,
    query_string: str,
    content_type: str | None,
    body: bytes,
    request_path: str,
    request_headers: dict[str, str] | None = None,
) -> Response:
    binary = php_cgi_path()
    if not binary:
        raise ServingError("PHP CGI runtime is not installed on this HomeServer.", 503)
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise ServingError("Hosted request body exceeds the runtime limit.", 413)

    public_root = hosting_deployment.active_public_root(site_id).resolve()
    env = _safe_runtime_environment()
    site=hosting_runtime.get_site(site_id)
    storage_root=(hosting_runtime.site_root(site_id)/"storage").resolve()
    sqlite_path=hosting_runtime.site_db_path(site_id).resolve()
    script_name="/" + str(request_path or target.name).replace("\\", "/").lstrip("/")
    hostname=str(site.get("requested_hostname") or "localhost")
    env.update({
        "GATEWAY_INTERFACE": "CGI/1.1",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "SERVER_SOFTWARE": "VP3-HomeServer",
        "SERVER_NAME": hostname,
        "SERVER_PORT": "80",
        "REMOTE_ADDR": "127.0.0.1",
        "REQUEST_METHOD": method,
        "QUERY_STRING": query_string,
        "REQUEST_URI": script_name + (("?" + query_string) if query_string else ""),
        "SCRIPT_FILENAME": str(target),
        "SCRIPT_NAME": script_name,
        "DOCUMENT_ROOT": str(public_root),
        "REDIRECT_STATUS": "1",
        "CONTENT_LENGTH": str(len(body)),
        "VP3_SITE_ID": site_id,
        "VP3_SQLITE_PATH": str(sqlite_path),
        "VP3_STORAGE_DIR": str(storage_root),
        "VP3_PUBLIC_ROOT": str(public_root),
    })
    incoming={str(k).lower():str(v) for k,v in (request_headers or {}).items()}
    forwarded={
        "accept":"HTTP_ACCEPT",
        "accept-language":"HTTP_ACCEPT_LANGUAGE",
        "user-agent":"HTTP_USER_AGENT",
        "referer":"HTTP_REFERER",
    }
    for header,env_key in forwarded.items():
        value=incoming.get(header)
        if value:
            env[env_key]=value[:4096]
    cookie=incoming.get("cookie","")
    if cookie:
        safe_parts=[]
        for part in cookie.split(";"):
            item=part.strip()
            if not item:
                continue
            name=item.split("=",1)[0].strip().lower()
            if name=="homeserver_owner":
                continue
            safe_parts.append(item)
        if safe_parts:
            env["HTTP_COOKIE"]="; ".join(safe_parts)[:8192]
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
    status, headers, cookies, response_body = _parse_cgi_output(completed.stdout)
    media_type = headers.pop("Content-Type", headers.pop("content-type", None))
    headers.setdefault("Cache-Control","no-store")
    headers.setdefault("X-Content-Type-Options","nosniff")
    response=Response(
        content=b"" if method == "HEAD" else response_body,
        status_code=status,
        headers=headers,
        media_type=media_type,
    )
    for cookie_value in cookies:
        response.headers.append("set-cookie",cookie_value)
    return response


def serve(
    site_id: str,
    request_path: str,
    *,
    method: str = "GET",
    query_string: str = "",
    content_type: str | None = None,
    body: bytes = b"",
    request_headers: dict[str, str] | None = None,
):
    site = hosting_runtime.get_site(site_id)
    method = str(method or "GET").upper()
    if method not in _ALLOWED_METHODS:
        raise ServingError("Hosted runtime method is not allowed.", 405)
    if site["state"] != "active":
        raise ServingError("Hosted site is not active.", 503)
    try:
        from . import hosting_cloud_control, homeserver_app_runtime
        binding=hosting_cloud_control.binding_for_site(site_id)
    except Exception:
        binding=None
    target_app_key=str((binding or {}).get("target_app_key") or "").strip().lower()
    if target_app_key=="vp3.media-server" and str(request_path or "").lstrip("/").startswith("__vp3_media__/"):
        try:
            from . import homeserver_media_server
            rel=str(request_path or "").lstrip("/")[len("__vp3_media__/"):]
            headers={str(k).lower():str(v) for k,v in (request_headers or {}).items()}
            auth=headers.get("authorization","")
            bearer=auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            query=parse_qs(str(query_string or ""),keep_blank_values=True)
            if rel.startswith("stream/"):
                media_id=rel.split("/",1)[1]
                ticket=str((query.get("ticket") or [""])[0])
                if not homeserver_media_server.authenticate_stream_ticket(media_id,ticket):
                    raise ServingError("Media Server stream ticket is invalid or expired.",401)
                path,mime,item=homeserver_media_server.resolve_stream(media_id)
                return FileResponse(
                    path,media_type=mime,filename=None,
                    headers={"Accept-Ranges":"bytes","Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-VP3-Media-Id":str(item["media_id"])},
                )
            if not homeserver_media_server.authenticate_remote(bearer):
                raise ServingError("Media Server access key is required.",401)
            if rel.startswith("playback/") and method=="POST":
                import json as _json
                media_id=rel.split("/",1)[1]
                try:
                    payload=_json.loads(body.decode("utf-8") or "{}")
                except Exception as exc:
                    raise ServingError("Media Server playback payload is invalid.",400) from exc
                result=homeserver_media_server.update_playback(
                    media_id,
                    float(payload.get("position_seconds") or 0),
                    float(payload.get("duration_seconds") or 0),
                    bool(payload.get("completed")),
                )
                return Response(content=_json.dumps(result),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel=="status":
                import json as _json
                return Response(content=_json.dumps(homeserver_media_server.status()),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel=="library":
                import json as _json
                q=str((query.get("q") or [""])[0])
                media_type=str((query.get("media_type") or [""])[0])
                try: limit=int((query.get("limit") or ["100"])[0])
                except ValueError: limit=100
                try: offset=int((query.get("offset") or ["0"])[0])
                except ValueError: offset=0
                return Response(content=_json.dumps(homeserver_media_server.library(q,media_type,limit,offset)),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel.startswith("item/"):
                import json as _json
                media_id=rel.split("/",1)[1]
                return Response(content=_json.dumps(homeserver_media_server.item(media_id)),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel.startswith("stream-ticket/"):
                import json as _json
                media_id=rel.split("/",1)[1]
                return Response(content=_json.dumps(homeserver_media_server.stream_ticket(media_id)),media_type="application/json",headers={"Cache-Control":"no-store"})
            raise ServingError("Media Server hosted route not found.",404)
        except ServingError:
            raise
        except homeserver_media_server.MediaServerError as exc:
            raise ServingError(str(exc),getattr(exc,"status_code",422)) from exc
    if target_app_key:
        try:
            return homeserver_app_runtime.serve(
                target_app_key,
                request_path,
                method=method,
                query_string=query_string,
                content_type=content_type,
                body=body,
            )
        except homeserver_app_runtime.AppRuntimeError as exc:
            raise ServingError(str(exc),getattr(exc,"status_code",422)) from exc

    target = _resolve_target(site_id, request_path)

    if target.suffix.lower() == ".php":
        if str(site["runtime_kind"]).lower() != "php":
            raise ServingError("PHP execution is not enabled for this site.", 403)
        hosting_scheduler.acquire(site_id)
        try:
            response=_execute_php(
                site_id,
                target,
                method=method,
                query_string=query_string,
                content_type=content_type,
                body=body,
                request_path=request_path,
                request_headers=request_headers,
            )
        except Exception:
            hosting_scheduler.release(site_id,failed=True)
            raise
        hosting_scheduler.release(site_id,failed=False)
        return response

    if method == "POST":
        raise ServingError("POST requests require a PHP entrypoint.",405)

    media_type, _ = mimetypes.guess_type(str(target))
    hosting_scheduler.acquire(site_id)
    try:
        return FileResponse(
            target,
            media_type=media_type or "application/octet-stream",
            filename=None,
            headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"},
            background=BackgroundTask(hosting_scheduler.release,site_id,False),
        )
    except Exception:
        hosting_scheduler.release(site_id,failed=True)
        raise


def runtime_health(site_id: str) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    deployment = hosting_deployment.deployment_status(site_id)
    active = deployment.get("active_release")
    try:
        from . import hosting_cloud_control, homeserver_app_runtime
        binding=hosting_cloud_control.binding_for_site(site_id)
    except Exception:
        binding=None
    target_app_key=str((binding or {}).get("target_app_key") or "").strip().lower()
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
        "scheduler": hosting_scheduler.status(site_id),
        "target_app_key":target_app_key or None,
        "target_kind":"system_app" if target_app_key else "deployment",
    }
    if target_app_key:
        if site["state"]!="active":
            return result
        try:
            app_status=homeserver_app_runtime.runtime_status(target_app_key)
            result["local_serving_ready"]=str(app_status.get("state") or "")=="running"
            result["app_runtime"]=app_status
        except Exception:
            result["local_serving_ready"]=False
        return result
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
        "scheduler": hosting_scheduler.public_capability(),
        "public_routing": False,
    }

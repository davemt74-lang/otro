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
    if target_app_key=="vp3.media-processor" and str(request_path or "").lstrip("/").startswith("__vp3_processor__/"):
        try:
            import json as _json
            from . import homeserver_media_processor
            rel=str(request_path or "").lstrip("/")[len("__vp3_processor__/"):]
            headers={str(k).lower():str(v) for k,v in (request_headers or {}).items()}
            auth=headers.get("authorization","")
            bearer=auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not homeserver_media_processor.authenticate_remote(bearer):
                raise ServingError("Media Processor access key is required.",401)
            query=parse_qs(str(query_string or ""),keep_blank_values=True)
            def _payload()->dict[str,Any]:
                if not body: return {}
                try: value=_json.loads(body.decode("utf-8") or "{}")
                except Exception as exc: raise ServingError("Media Processor request payload is invalid.",400) from exc
                if not isinstance(value,dict): raise ServingError("Media Processor request payload must be an object.",400)
                return value
            def _response(value:Any)->Response:
                return Response(content=_json.dumps(value),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel=="status" and method=="GET": return _response(homeserver_media_processor.status())
            if rel=="brain-context" and method=="GET": return _response(homeserver_media_processor.brain_context())
            if rel=="tools" and method=="GET": return _response(homeserver_media_processor.capability())
            if rel=="jobs" and method=="GET":
                try: limit=int((query.get("limit") or ["100"])[0])
                except ValueError: limit=100
                return _response(homeserver_media_processor.list_jobs(limit))
            if rel=="jobs" and method=="POST":
                p=_payload()
                return _response(homeserver_media_processor.enqueue(
                    str(p.get("media_id") or ""),str(p.get("operation") or ""),
                    str(p.get("preset") or "default"),str(p.get("output_format") or ""),
                    int(p.get("priority") or 0),str(p.get("destination_id") or "app-storage"),
                ))
            if rel.startswith("derivatives/") and rel.endswith("/file") and method=="GET":
                derivative_id=rel.split("/")[1]
                path,row=homeserver_media_processor.resolve_derivative(derivative_id)
                mime={
                    "mp4":"video/mp4","webm":"video/webm","mp3":"audio/mpeg",
                    "jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"
                }.get(str(row.get("format") or "").lower(),"application/octet-stream")
                return FileResponse(
                    path,media_type=mime,filename=None,
                    headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff","X-VP3-Derivative-Id":str(row["derivative_id"])},
                )
            if rel=="derivatives" and method=="GET":
                media_id=str((query.get("media_id") or [""])[0])
                try: limit=int((query.get("limit") or ["200"])[0])
                except ValueError: limit=200
                return _response(homeserver_media_processor.derivatives(media_id,limit))
            if rel=="destinations" and method=="GET": return _response(homeserver_media_processor.destinations())
            if rel=="settings" and method=="GET": return _response(homeserver_media_processor.settings())
            if rel.startswith("jobs/"):
                parts=rel.split("/")
                job_id=parts[1] if len(parts)>1 else ""
                if len(parts)==2 and method=="GET": return _response(homeserver_media_processor.get_job(job_id))
                if len(parts)==2 and method=="DELETE": return _response(homeserver_media_processor.cancel(job_id))
                if len(parts)==3 and parts[2]=="retry" and method=="POST": return _response(homeserver_media_processor.retry(job_id))
            raise ServingError("Media Processor hosted route not found.",404)
        except ServingError:
            raise
        except homeserver_media_processor.MediaProcessorError as exc:
            raise ServingError(str(exc),getattr(exc,"status_code",422)) from exc
    if target_app_key=="vp3.download-manager" and str(request_path or "").lstrip("/").startswith("__vp3_downloads__/"):
        try:
            import json as _json
            from . import homeserver_download_manager
            rel=str(request_path or "").lstrip("/")[len("__vp3_downloads__/"):]
            headers={str(k).lower():str(v) for k,v in (request_headers or {}).items()}
            auth=headers.get("authorization","")
            bearer=auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not homeserver_download_manager.authenticate_remote(bearer):
                raise ServingError("Download Manager access key is required.",401)
            query=parse_qs(str(query_string or ""),keep_blank_values=True)
            def _payload()->dict[str,Any]:
                if not body: return {}
                try: value=_json.loads(body.decode("utf-8") or "{}")
                except Exception as exc: raise ServingError("Download Manager request payload is invalid.",400) from exc
                if not isinstance(value,dict): raise ServingError("Download Manager request payload must be an object.",400)
                return value
            def _response(value:Any)->Response:
                return Response(content=_json.dumps(value),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel=="status" and method=="GET": return _response(homeserver_download_manager.status())
            if rel=="brain-context" and method=="GET": return _response(homeserver_download_manager.brain_context())
            if rel=="destinations" and method=="GET": return _response(homeserver_download_manager.destinations())
            if rel=="settings" and method=="GET": return _response(homeserver_download_manager.settings())
            if rel=="downloads" and method=="GET":
                status=str((query.get("status") or [""])[0])
                try: limit=int((query.get("limit") or ["200"])[0])
                except ValueError: limit=200
                return _response(homeserver_download_manager.list_downloads(status,limit))
            if rel=="downloads" and method=="POST":
                p=_payload()
                return _response(homeserver_download_manager.enqueue(
                    str(p.get("url") or ""),destination_id=str(p.get("destination_id") or "app-storage"),
                    filename=str(p.get("filename") or ""),priority=int(p.get("priority") or 0),
                    checksum_algorithm=str(p.get("checksum_algorithm") or ""),
                    checksum_expected=str(p.get("checksum_expected") or ""),
                    max_retries=int(p.get("max_retries") if p.get("max_retries") is not None else 3),
                    scheduled_at=p.get("scheduled_at"),
                ))
            if rel=="history" and method=="DELETE": return _response(homeserver_download_manager.clear_history())
            if rel.startswith("downloads/"):
                parts=rel.split("/")
                download_id=parts[1] if len(parts)>1 else ""
                if len(parts)==2 and method=="GET": return _response(homeserver_download_manager.get_download(download_id))
                if len(parts)==2 and method=="DELETE": return _response(homeserver_download_manager.cancel(download_id))
                if len(parts)==3 and parts[2]=="pause" and method=="POST": return _response(homeserver_download_manager.pause(download_id))
                if len(parts)==3 and parts[2]=="resume" and method=="POST": return _response(homeserver_download_manager.resume(download_id))
                if len(parts)==3 and parts[2]=="retry" and method=="POST": return _response(homeserver_download_manager.retry(download_id))
                if len(parts)==3 and parts[2]=="priority" and method=="PUT":
                    return _response(homeserver_download_manager.set_priority(download_id,int(_payload().get("priority") or 0)))
            raise ServingError("Download Manager hosted route not found.",404)
        except ServingError:
            raise
        except homeserver_download_manager.DownloadManagerError as exc:
            raise ServingError(str(exc),getattr(exc,"status_code",422)) from exc
    if target_app_key=="vp3.photo-library" and str(request_path or "").lstrip("/").startswith("__vp3_photos__/"):
        try:
            import json as _json
            from . import homeserver_media_server, homeserver_photo_library
            rel=str(request_path or "").lstrip("/")[len("__vp3_photos__/"):]
            headers={str(k).lower():str(v) for k,v in (request_headers or {}).items()}
            auth=headers.get("authorization","")
            bearer=auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            query=parse_qs(str(query_string or ""),keep_blank_values=True)
            if rel.startswith("stream/"):
                media_id=rel.split("/",1)[1]
                ticket=str((query.get("ticket") or [""])[0])
                if not homeserver_media_server.authenticate_stream_ticket(media_id,ticket):
                    raise ServingError("Photo Library stream ticket is invalid or expired.",401)
                path,mime,item=homeserver_media_server.resolve_stream(media_id)
                if str(item.get("media_type") or "")!="image":
                    raise ServingError("Photo Library can stream image items only.",415)
                return FileResponse(
                    path,media_type=mime,filename=None,
                    headers={
                        "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
                        "Referrer-Policy":"no-referrer","X-VP3-Media-Id":str(item["media_id"]),
                    },
                )
            if not homeserver_media_server.authenticate_remote(bearer):
                raise ServingError("Photo Library access key is required.",401)
            def _payload()->dict[str,Any]:
                if not body:
                    return {}
                try:
                    value=_json.loads(body.decode("utf-8") or "{}")
                except Exception as exc:
                    raise ServingError("Photo Library request payload is invalid.",400) from exc
                if not isinstance(value,dict):
                    raise ServingError("Photo Library request payload must be an object.",400)
                return value
            def _response(value:Any)->Response:
                return Response(content=_json.dumps(value),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel=="status" and method=="GET":
                return _response(homeserver_photo_library.status())
            if rel=="sync" and method=="POST":
                return _response(homeserver_photo_library.sync())
            if rel=="photos" and method=="GET":
                q=str((query.get("q") or [""])[0])
                folder=str((query.get("folder_album") or [""])[0])
                tag=str((query.get("tag") or [""])[0])
                favorites=str((query.get("favorites_only") or ["false"])[0]).lower() in {"1","true","yes"}
                try: limit=int((query.get("limit") or ["200"])[0])
                except ValueError: limit=200
                try: offset=int((query.get("offset") or ["0"])[0])
                except ValueError: offset=0
                return _response(homeserver_photo_library.photos(q,folder,tag,favorites,limit,offset))
            if rel=="folders" and method=="GET":
                return _response(homeserver_photo_library.folders())
            if rel=="timeline" and method=="GET":
                try: limit=int((query.get("limit") or ["500"])[0])
                except ValueError: limit=500
                return _response(homeserver_photo_library.timeline(limit))
            if rel=="albums" and method=="GET":
                return _response(homeserver_photo_library.albums())
            if rel=="albums" and method=="POST":
                return _response(homeserver_photo_library.create_album(str(_payload().get("name") or "")))
            if rel=="smart-albums" and method=="GET":
                return _response(homeserver_photo_library.smart_albums())
            if rel=="tags" and method=="GET":
                return _response(homeserver_photo_library.tags())
            if rel=="people" and method=="GET":
                return _response(homeserver_photo_library.people())
            if rel=="duplicates" and method=="GET":
                try: limit=int((query.get("limit") or ["100"])[0])
                except ValueError: limit=100
                return _response(homeserver_photo_library.duplicate_groups(limit))
            if rel=="slideshow" and method=="GET":
                q=str((query.get("q") or [""])[0])
                folder=str((query.get("folder_album") or [""])[0])
                try: limit=int((query.get("limit") or ["200"])[0])
                except ValueError: limit=200
                return _response(homeserver_photo_library.slideshow(q,folder,limit))
            if rel.startswith("stream-ticket/") and method=="GET":
                media_id=rel.split("/",1)[1]
                ticket=homeserver_media_server.stream_ticket(media_id)
                return _response({
                    **ticket,
                    "stream_url":f"/__vp3_photos__/stream/{media_id}?ticket={ticket['ticket']}",
                })
            raise ServingError("Photo Library hosted route not found.",404)
        except ServingError:
            raise
        except (homeserver_photo_library.PhotoLibraryError,homeserver_media_server.MediaServerError) as exc:
            raise ServingError(str(exc),getattr(exc,"status_code",422)) from exc
    if target_app_key=="vp3.music-server" and str(request_path or "").lstrip("/").startswith("__vp3_music__/"):
        try:
            import json as _json
            from . import homeserver_media_server, homeserver_music_server
            rel=str(request_path or "").lstrip("/")[len("__vp3_music__/"):]
            headers={str(k).lower():str(v) for k,v in (request_headers or {}).items()}
            auth=headers.get("authorization","")
            bearer=auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            query=parse_qs(str(query_string or ""),keep_blank_values=True)
            if rel.startswith("stream/"):
                media_id=rel.split("/",1)[1]
                ticket=str((query.get("ticket") or [""])[0])
                if not homeserver_media_server.authenticate_stream_ticket(media_id,ticket):
                    raise ServingError("Music Server stream ticket is invalid or expired.",401)
                path,mime,item=homeserver_media_server.resolve_stream(media_id)
                if str(item.get("media_type") or "")!="audio":
                    raise ServingError("Music Server can stream audio items only.",415)
                return FileResponse(
                    path,media_type=mime,filename=None,
                    headers={
                        "Accept-Ranges":"bytes","Cache-Control":"private, no-store",
                        "X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
                        "X-VP3-Media-Id":str(item["media_id"]),
                    },
                )
            if not homeserver_media_server.authenticate_remote(bearer):
                raise ServingError("Music Server access key is required.",401)
            def _payload()->dict[str,Any]:
                if not body:
                    return {}
                try:
                    value=_json.loads(body.decode("utf-8") or "{}")
                except Exception as exc:
                    raise ServingError("Music Server request payload is invalid.",400) from exc
                if not isinstance(value,dict):
                    raise ServingError("Music Server request payload must be an object.",400)
                return value
            def _response(value:Any)->Response:
                return Response(content=_json.dumps(value),media_type="application/json",headers={"Cache-Control":"no-store"})
            if rel=="status" and method=="GET":
                return _response(homeserver_music_server.status())
            if rel=="sync" and method=="POST":
                return _response(homeserver_music_server.sync())
            if rel=="tracks" and method=="GET":
                q=str((query.get("q") or [""])[0])
                artist=str((query.get("artist") or [""])[0])
                album=str((query.get("album") or [""])[0])
                try: limit=int((query.get("limit") or ["200"])[0])
                except ValueError: limit=200
                return _response(homeserver_music_server.tracks(q,artist,album,limit))
            if rel=="artists" and method=="GET":
                try: limit=int((query.get("limit") or ["500"])[0])
                except ValueError: limit=500
                return _response(homeserver_music_server.artists(limit))
            if rel=="albums" and method=="GET":
                artist=str((query.get("artist") or [""])[0])
                try: limit=int((query.get("limit") or ["500"])[0])
                except ValueError: limit=500
                return _response(homeserver_music_server.albums(artist,limit))
            if rel=="favorites" and method=="GET":
                return _response(homeserver_music_server.favorites())
            if rel.startswith("favorites/") and method in {"PUT","POST"}:
                media_id=rel.split("/",1)[1]
                payload=_payload()
                return _response(homeserver_music_server.favorite(media_id,bool(payload.get("enabled",True))))
            if rel=="playlists" and method=="GET":
                return _response(homeserver_music_server.playlists())
            if rel=="playlists" and method=="POST":
                return _response(homeserver_music_server.create_playlist(str(_payload().get("name") or "")))
            if rel.startswith("playlists/"):
                parts=rel.split("/")
                playlist_id=parts[1] if len(parts)>1 else ""
                if len(parts)==2 and method=="GET":
                    return _response(homeserver_music_server.playlist(playlist_id))
                if len(parts)==2 and method=="DELETE":
                    return _response(homeserver_music_server.delete_playlist(playlist_id))
                if len(parts)==3 and parts[2]=="tracks" and method=="POST":
                    return _response(homeserver_music_server.playlist_add(playlist_id,str(_payload().get("media_id") or "")))
                if len(parts)==4 and parts[2]=="tracks" and method=="DELETE":
                    return _response(homeserver_music_server.playlist_remove(playlist_id,parts[3]))
            if rel=="queue" and method=="GET":
                return _response(homeserver_music_server.queue())
            if rel=="queue" and method=="POST":
                return _response(homeserver_music_server.queue_add(str(_payload().get("media_id") or "")))
            if rel=="queue" and method=="DELETE":
                return _response(homeserver_music_server.queue_clear())
            if rel=="playback" and method=="POST":
                payload=_payload()
                return _response(homeserver_music_server.playback(
                    str(payload.get("command") or ""),
                    str(payload.get("media_id") or ""),
                    float(payload.get("position_seconds") or 0),
                ))
            if rel.startswith("stream-ticket/") and method=="GET":
                media_id=rel.split("/",1)[1]
                ticket=homeserver_media_server.stream_ticket(media_id)
                return _response({
                    **ticket,
                    "stream_url":f"/__vp3_music__/stream/{media_id}?ticket={ticket['ticket']}",
                })
            raise ServingError("Music Server hosted route not found.",404)
        except ServingError:
            raise
        except (homeserver_music_server.MusicServerError,homeserver_media_server.MediaServerError) as exc:
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

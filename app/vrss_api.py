"""Separate owner-only VRSS control and opt-in, read-only public documents."""
from __future__ import annotations

import html
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .services import vrss

router = APIRouter(tags=["vrss"])
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}


def call(fn, *args):
    try:
        return fn(*args)
    except vrss.VRSSError as exc:
        raise HTTPException(exc.status_code, str(exc), headers=HEADERS) from exc


@router.post("/api/v1/control/homeserver-apps/vrss/request")
async def owner_request(request: Request):
    # Custom header and JSON block cross-site form submission with the owner cookie.
    if request.headers.get("x-vrss-request") != "1" or request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(403, "VRSS requests require the local application.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(413, "VRSS request is too large.")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "A VRSS request is required.") from exc
    return JSONResponse(await run_in_threadpool(call, vrss.request, body), headers=HEADERS)


def public_html(doc, post=False):
    e = html.escape
    if post:
        title = doc["author"]["name"]
        content = "<p>" + e(doc["body"]).replace("\n", "<br>") + "</p>"
        for media in doc["media"]:
            content += '<p><a rel="noreferrer noopener" href="' + e(media["url"], quote=True) + '">' + e(media["alt"] or "Open " + media["kind"]) + '</a></p>'
        content += '<a href="' + e(doc["author"]["url"], quote=True) + '">Profile</a>'
    else:
        title = doc["name"]
        content = "<p>" + e(doc["bio"]).replace("\n", "<br>") + '</p><p><a href="' + e(doc["rss"], quote=True) + '">Follow RSS feed</a></p><p>Copy this feed address into your reader:</p><p style="overflow-wrap:anywhere">' + e(doc["rss"]) + "</p>"
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + e(title) + '</title><style>body{max-width:720px;margin:48px auto;padding:20px;font:17px/1.6 system-ui;color:#18222f}a{color:#315cc5}p{white-space:pre-wrap;overflow-wrap:anywhere}</style><main><h1>' + e(title) + "</h1>" + content + "</main></html>"


def document_response(kind, post_id="", accept=""):
    doc = vrss.public_document(kind, post_id)
    if kind == "rss":
        return Response(doc, media_type="application/rss+xml", headers=HEADERS)
    if "text/html" in accept:
        return HTMLResponse(public_html(doc, kind == "post"), headers=HEADERS)
    return JSONResponse(doc, headers=HEADERS)


def hosted_response(site_id, request_path, method="GET", headers=None):
    """Allowlisted public surface, reached after the existing Hosting authority."""
    from .services import hosting_runtime
    if method not in {"GET", "HEAD"}:
        raise vrss.VRSSError("VRSS public Hosting is read-only.", 405)
    # Accept only public documents; never fall through to owner app assets or controls.
    path = str(request_path or "")
    if not path.startswith("/"):
        path = "/" + path
    post_id = ""
    if path in {"/", "/vrss/v1/profile"}:
        kind = "profile"
    elif path == "/vrss/v1/rss":
        kind = "rss"
    elif re.fullmatch(r"/vrss/v1/posts/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", path):
        kind, post_id = "post", path.rsplit("/", 1)[1]
    else:
        raise vrss.VRSSError("Public VRSS document not found.", 404)
    with vrss.database() as c:
        profile = vrss.profile(c)
    site = hosting_runtime.get_site(site_id)
    base = "https://" + str(site.get("requested_hostname") or "")
    if profile["public_base"] != base or profile["hosting_site_id"] != site_id:
        raise vrss.VRSSError("This Hosting site is not the published VRSS address.", 404)
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    response = document_response(kind, post_id, "application/json" if "application/json" in headers.get("accept", "") else "text/html")
    if method == "HEAD":
        response.body = b""
    return response


@router.get("/vrss/v1/profile")
def public_profile(request: Request):
    return call(document_response, "profile", "", request.headers.get("accept", ""))


@router.get("/vrss/v1/rss")
def public_rss():
    return call(document_response, "rss")


@router.get("/vrss/v1/posts/{post_id}")
def public_post(post_id: str, request: Request):
    return call(document_response, "post", post_id, request.headers.get("accept", ""))

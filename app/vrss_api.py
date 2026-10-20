"""Separate owner-only VRSS control and opt-in, read-only public documents."""
from __future__ import annotations

import html
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .services import vrss, vrss_social

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
    if not post and doc.get("social"):
        content += '<p>To follow this verified VRSS profile, ask your HomeServer Agent Chat:</p><p>Follow VRSS profile ' + e(doc['url']) + '</p>'
    rss_link = ('<link rel="alternate" type="application/rss+xml" href="' + e(doc['rss'], quote=True) + '">') if not post else ''
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + rss_link + '<title>' + e(title) + '</title><style>body{max-width:720px;margin:48px auto;padding:20px;font:17px/1.6 system-ui;color:#18222f}a{color:#315cc5}p{white-space:pre-wrap;overflow-wrap:anywhere}</style><main><h1>' + e(title) + "</h1>" + content + "</main></html>"


def document_response(kind, post_id="", accept="", after="0"):
    if kind == 'repost':
        from .services import vrss_reposts
        with vrss.database() as c:
            p = vrss.profile(c)
        doc = vrss_reposts.public_pointer(p['public_base'] + '/vrss/v1/reposts/' + post_id)
        if 'text/html' in accept:
            e = html.escape
            content = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VRSS repost</title><style>body{max-width:720px;margin:48px auto;padding:20px;font:17px/1.6 system-ui;overflow-wrap:anywhere}</style><main><h1>Reposted by ' + e(doc['author']['name']) + '</h1><p>Original author: ' + e(doc['original']['name']) + '</p><p><a rel="noreferrer noopener" href="' + e(doc['original']['url'], quote=True) + '">Read the original post</a></p></main></html>'
            return HTMLResponse(content, headers=HEADERS)
        return JSONResponse(doc, headers=HEADERS)
    doc = vrss_social.actor_document() if kind == "actor" else vrss.public_document(kind, post_id, after)
    if kind == "rss":
        return Response(doc, media_type="application/rss+xml", headers=HEADERS)
    if kind not in {"actor", "comments", "interactions", "reposts"} and "text/html" in accept:
        return HTMLResponse(public_html(doc, kind == "post"), headers=HEADERS)
    return JSONResponse(doc, headers=HEADERS)


def hosted_response(site_id, request_path, method="GET", headers=None, body=b""):
    """Allowlisted public surface, reached after the existing Hosting authority."""
    from .services import hosting_runtime
    # Accept only public documents; never fall through to owner app assets or controls.
    path = str(request_path or "")
    if not path.startswith("/"):
        path = "/" + path
    if method not in {"GET", "HEAD"} and not (method == "POST" and path == "/vrss/v1/inbox"):
        raise vrss.VRSSError("This VRSS path does not accept writes.", 405)
    post_id = ""
    after = '0'
    if path in {"/", "/vrss/v1/profile"}:
        kind = "profile"
    elif path in {"/vrss/v1/actor", "/vrss/v1/inbox"}:
        kind = path.rsplit("/", 1)[1]
    elif path == "/vrss/v1/rss":
        kind = "rss"
    elif re.fullmatch(r"/vrss/v1/posts/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", path):
        kind, post_id = "post", path.rsplit("/", 1)[1]
    elif re.fullmatch(r"/vrss/v1/reposts/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", path):
        kind, post_id = 'repost', path.rsplit('/', 1)[1]
    elif re.fullmatch(r"/vrss/v1/posts/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/reposts(?:/id-[0-9a-f]{64})?", path):
        parts = path.split('/')
        kind, post_id = 'reposts', parts[4]
        if len(parts) == 7:
            after = parts[6]
    elif re.fullmatch(r"/vrss/v1/posts/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/interactions", path):
        kind, post_id = "interactions", path.split('/')[-2]
    elif re.fullmatch(r"/vrss/v1/posts/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/comments(?:/(?:[1-9][0-9]{0,15}|id-[0-9a-f]{64}))?", path):
        parts = path.split('/')
        kind, post_id = 'comments', parts[4]
        if len(parts) == 7:
            after = parts[6]
    else:
        raise vrss.VRSSError("Public VRSS document not found.", 404)
    with vrss.database() as c:
        profile = vrss.profile(c)
    site = hosting_runtime.get_site(site_id)
    base = "https://" + str(site.get("requested_hostname") or "")
    if profile["public_base"] != base or profile["hosting_site_id"] != site_id:
        raise vrss.VRSSError("This Hosting site is not the published VRSS address.", 404)
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if kind == "inbox":
        if method != "POST":
            raise vrss.VRSSError("Use signed POST activities for this inbox.", 405)
        if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise vrss.VRSSError("A JSON VRSS activity is required.", 415)
        return JSONResponse(vrss_social.receive(body), status_code=202, headers=HEADERS)
    response = document_response(kind, post_id, "application/json" if kind in {"interactions", "comments", "reposts"} or "application/json" in headers.get("accept", "") else "text/html", after)
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


@router.get("/vrss/v1/posts/{post_id}/interactions")
def public_interactions(post_id: str):
    return call(document_response, "interactions", post_id)


@router.get('/vrss/v1/reposts/{repost_id}')
def public_repost(repost_id: str, request: Request):
    return call(document_response, 'repost', repost_id, request.headers.get('accept', ''))


@router.get('/vrss/v1/posts/{post_id}/reposts')
def public_repost_count(post_id: str):
    return call(document_response, 'reposts', post_id)


@router.get('/vrss/v1/posts/{post_id}/reposts/{after}')
def public_repost_receipt(post_id: str, after: str):
    return call(document_response, 'reposts', post_id, '', after)


@router.get("/vrss/v1/actor")
def public_actor():
    return call(document_response, "actor")


@router.get('/vrss/v1/posts/{post_id}/comments')
def public_comments(post_id: str):
    return call(document_response, 'comments', post_id)


@router.get('/vrss/v1/posts/{post_id}/comments/{after}')
def public_comments_page(post_id: str, after: str):
    return call(document_response, 'comments', post_id, '', after)


@router.post("/vrss/v1/inbox")
async def public_inbox(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "A JSON VRSS activity is required.", headers=HEADERS)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > vrss_social.MAX_BODY:
            raise HTTPException(413, "VRSS activity is too large.", headers=HEADERS)
    return JSONResponse(await run_in_threadpool(call, vrss_social.receive, bytes(raw)), status_code=202, headers=HEADERS)

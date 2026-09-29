from __future__ import annotations

import time
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from .services import hosting_diagnostics, hosting_public, hosting_serving

router=APIRouter(tags=["hosting-public-ingress"])


def _call(fn,*args,**kwargs):
    try:
        return fn(*args,**kwargs)
    except hosting_public.PublicRoutingError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
    except hosting_serving.ServingError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.api_route(
    "/__vp3/hosting/{request_path:path}",
    methods=["GET","HEAD","POST"],
    include_in_schema=False,
)
async def public_ingress(
    request_path:str,
    request:Request,
    route_token:str=Header(alias="X-VP3-Route-Token"),
    forwarded_host:str=Header(alias="X-Forwarded-Host"),
    forwarded_proto:str=Header(alias="X-Forwarded-Proto"),
):
    proto=str(forwarded_proto or "").strip().lower()
    if proto not in {"http","https"}:
        raise HTTPException(status_code=400,detail="X-Forwarded-Proto must be http or https.")

    if proto=="http":
        route=_call(hosting_public.authorize_ingress,forwarded_host,route_token,"https")
        suffix="/" + quote(str(request_path or ""),safe="/:@-._~!$&'()*+,;=")
        if request.url.query:
            suffix += "?" + request.url.query
        return RedirectResponse(
            url=f"https://{route['hostname']}{suffix}",
            status_code=308,
            headers={"Cache-Control":"no-store"},
        )

    route=_call(hosting_public.authorize_ingress,forwarded_host,route_token,"https")
    content_length=request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length)>hosting_serving.MAX_REQUEST_BODY_BYTES:
                raise HTTPException(status_code=413,detail="Hosted request body exceeds the runtime limit.")
        except ValueError as exc:
            raise HTTPException(status_code=400,detail="Invalid Content-Length header.") from exc
    body=await request.body()
    if len(body)>hosting_serving.MAX_REQUEST_BODY_BYTES:
        raise HTTPException(status_code=413,detail="Hosted request body exceeds the runtime limit.")

    started=time.monotonic()
    try:
        response=_call(
            hosting_serving.serve,
            route["site_id"],
            request_path,
            method=request.method,
            query_string=request.url.query,
            content_type=request.headers.get("content-type"),
            body=body,
            request_headers=dict(request.headers),
        )
    except HTTPException as exc:
        detail=str(exc.detail or "").lower()
        hosting_diagnostics.observe_request(
            route["site_id"],source="public",method=request.method,path=request_path,
            status_code=int(exc.status_code),duration_ms=(time.monotonic()-started)*1000,
            error_class="php.runtime" if "php" in detail else "http.error",
        )
        raise
    status=int(getattr(response,"status_code",200))
    size=len(getattr(response,"body",b"") or b"")
    hosting_diagnostics.observe_request(
        route["site_id"],source="public",method=request.method,path=request_path,
        status_code=status,duration_ms=(time.monotonic()-started)*1000,response_bytes=size,
        error_class="",
    )
    return response

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .services import hosting_cloud_deployment, hosting_deployment, hosting_entitlements, hosting_operations, hosting_public, hosting_recovery, hosting_runtime, hosting_scheduler, hosting_serving, hosting_sqlite

router=APIRouter(prefix="/api/v1/control/hosting",tags=["hosting"])


class CreateSiteRequest(BaseModel):
    display_name: str = Field(min_length=1,max_length=160)
    requested_hostname: str | None = Field(default=None,max_length=253)
    runtime_kind: str = Field(default="static",max_length=20)
    storage_limit_bytes: int | None = Field(default=None,ge=0)
    sqlite_limit_bytes: int | None = Field(default=None,ge=0)


class StateRequest(BaseModel):
    state: str = Field(min_length=1,max_length=20)


class HostingOperationRequest(BaseModel):
    action: str = Field(min_length=3,max_length=80)
    idempotency_key: str = Field(min_length=8,max_length=160)
    confirmed: bool = False


def _call(fn,*args,**kwargs):
    try:
        return fn(*args,**kwargs)
    except hosting_runtime.HostingError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/capability")
def capability() -> dict:
    result=hosting_runtime.public_capability()
    result["sqlite_runtime"]=hosting_sqlite.public_capability()
    result["cloud_deployment"]=hosting_cloud_deployment.public_capability()
    result["public_routing"]=hosting_public.public_capability()
    result["entitlements"]=hosting_entitlements.public_capability()
    result["operations"]=hosting_operations.public_capability()
    result["scheduler"]=hosting_scheduler.public_capability()
    return result



@router.get("/dashboard")
def dashboard() -> dict:
    return _call(hosting_operations.dashboard)


@router.post("/sites/{site_id}/operations")
def execute_operation(site_id: str,payload: HostingOperationRequest) -> dict:
    return _call(hosting_operations.execute,site_id,payload.action,payload.idempotency_key,confirmed=payload.confirmed)


@router.get("/sites")
def sites() -> dict:
    return {"sites":hosting_runtime.list_sites()}


@router.post("/sites")
def create_site(payload: CreateSiteRequest) -> dict:
    return _call(
        hosting_runtime.create_site,
        payload.display_name,
        requested_hostname=payload.requested_hostname,
        runtime_kind=payload.runtime_kind,
        storage_limit_bytes=payload.storage_limit_bytes,
        sqlite_limit_bytes=payload.sqlite_limit_bytes,
    )


@router.get("/sites/{site_id}")
def site(site_id: str) -> dict:
    item=_call(hosting_runtime.get_site,site_id)
    item["usage"]=_call(hosting_runtime.sample_usage,site_id)
    item["database_health"]=_call(hosting_runtime.database_health,site_id)
    item["sqlite_runtime"]=_call(hosting_sqlite.schema_status,site_id)
    item["public_route"]=_call(hosting_public.route_status_for_site,site_id)
    item["entitlements"]=hosting_entitlements.status()
    return item


@router.post("/sites/{site_id}/state")
def site_state(site_id: str,payload: StateRequest) -> dict:
    return _call(hosting_runtime.set_state,site_id,payload.state)


@router.post("/sites/{site_id}/backups")
def backup(site_id: str) -> dict:
    return _call(hosting_runtime.create_backup,site_id)


@router.get("/sites/{site_id}/sqlite")
def sqlite_status(site_id: str) -> dict:
    return _call(hosting_sqlite.schema_status,site_id)


@router.get("/sites/{site_id}/deployments")
def deployments(site_id: str) -> dict:
    return {
        "status":_call(hosting_deployment.deployment_status,site_id),
        "releases":_call(hosting_deployment.list_releases,site_id),
    }


@router.post("/sites/{site_id}/deployments")
async def deploy(
    site_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None,alias="Idempotency-Key"),
) -> dict:
    content_type=str(request.headers.get("content-type") or "").split(";",1)[0].strip().lower()
    if content_type not in {"application/zip","application/octet-stream"}:
        raise HTTPException(status_code=415,detail="Deployment body must be a ZIP package.")
    content_length=request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length)>hosting_deployment.MAX_PACKAGE_BYTES:
                raise HTTPException(status_code=413,detail="Deployment package exceeds the compressed size limit.")
        except ValueError:
            raise HTTPException(status_code=400,detail="Invalid Content-Length header.")
    package=await request.body()
    return _call(hosting_deployment.deploy_package,site_id,package,request_key=idempotency_key)


@router.post("/sites/{site_id}/deployments/rollback")
def rollback(site_id: str) -> dict:
    return _call(hosting_deployment.rollback,site_id)


@router.get("/sites/{site_id}/runtime-health")
def runtime_health(site_id: str) -> dict:
    return _call(hosting_serving.runtime_health,site_id)


@router.get("/sites/{site_id}/recovery")
def recovery_points(site_id: str) -> dict:
    return {
        "health":_call(hosting_recovery.recovery_health,site_id),
        "points":_call(hosting_recovery.list_recovery_points,site_id),
    }


@router.post("/sites/{site_id}/recovery")
def create_recovery_point(site_id: str) -> dict:
    return _call(hosting_recovery.create_recovery_point,site_id,reason="owner-manual")


@router.post("/sites/{site_id}/recovery/{recovery_id}/verify")
def verify_recovery_point(site_id: str,recovery_id: str) -> dict:
    return _call(hosting_recovery.verify,site_id,recovery_id)


@router.post("/sites/{site_id}/recovery/{recovery_id}/restore")
def restore_recovery_point(site_id: str,recovery_id: str) -> dict:
    return _call(hosting_recovery.restore,site_id,recovery_id)


@router.api_route(
    "/sites/{site_id}/preview/{request_path:path}",
    methods=["GET","HEAD","POST"],
    include_in_schema=False,
)
async def preview(site_id: str, request_path: str, request: Request):
    body=await request.body()
    return _call(
        hosting_serving.serve,
        site_id,
        request_path,
        method=request.method,
        query_string=request.url.query,
        content_type=request.headers.get("content-type"),
        body=body,
        request_headers=dict(request.headers),
    )

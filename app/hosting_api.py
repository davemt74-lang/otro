from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .services import hosting_runtime

router=APIRouter(prefix="/api/v1/control/hosting",tags=["hosting"])


class CreateSiteRequest(BaseModel):
    display_name: str = Field(min_length=1,max_length=160)
    requested_hostname: str | None = Field(default=None,max_length=253)
    runtime_kind: str = Field(default="static",max_length=20)
    storage_limit_bytes: int | None = Field(default=None,ge=0)
    sqlite_limit_bytes: int | None = Field(default=None,ge=0)


class StateRequest(BaseModel):
    state: str = Field(min_length=1,max_length=20)


def _call(fn,*args,**kwargs):
    try:
        return fn(*args,**kwargs)
    except hosting_runtime.HostingError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/capability")
def capability() -> dict:
    return hosting_runtime.public_capability()


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
    return item


@router.post("/sites/{site_id}/state")
def site_state(site_id: str,payload: StateRequest) -> dict:
    return _call(hosting_runtime.set_state,site_id,payload.state)


@router.post("/sites/{site_id}/backups")
def backup(site_id: str) -> dict:
    return _call(hosting_runtime.create_backup,site_id)

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .services import local_apps

router = APIRouter(prefix="/api/v1/control/local-apps", tags=["local-apps"])


def _call(operation, *args, **kwargs):  # noqa: ANN001, ANN201
    try:
        return operation(*args, **kwargs)
    except local_apps.LocalAppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("")
def list_local_apps() -> dict:
    return local_apps.catalog()


@router.post("/{app_key}/install")
def install_local_app(app_key: str) -> dict:
    return _call(local_apps.install, app_key, update=False)


@router.post("/{app_key}/update")
def update_local_app(app_key: str) -> dict:
    return _call(local_apps.install, app_key, update=True)


@router.delete("/{app_key}")
def uninstall_local_app(app_key: str) -> dict:
    return _call(local_apps.uninstall, app_key)

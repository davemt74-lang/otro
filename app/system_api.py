from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings
from .database import db
from .payments_api import router as payments_router
from .services.runtime_control import request_runtime_command
from .services.system_state import set_first_run_complete, system_summary
from .services.vp3_commerce_remote import install as install_vp3_commerce_remote
from .services.windows_integration import WindowsIntegrationError, open_folder, set_startup_enabled

router = APIRouter()
router.include_router(payments_router)
install_vp3_commerce_remote()
UI_DIR = Path(__file__).resolve().parents[1] / "ui"


class StartupUpdate(BaseModel):
    enabled: bool


class SetupUpdate(BaseModel):
    complete: bool = True


def _audit(action: str, metadata: dict | None = None) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', ?, 'system', 'windows-runtime', ?)
            """,
            (action, json.dumps(metadata or {}, separators=(",", ":"))),
        )


@router.get("/system", include_in_schema=False)
def system_page():
    path = UI_DIR / "system.html"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="System workspace assets are unavailable")
    return FileResponse(path)


@router.get("/api/v1/control/system")
def control_system() -> dict:
    return system_summary()


@router.put("/api/v1/control/system/startup")
def control_startup(payload: StartupUpdate) -> dict:
    try:
        result = set_startup_enabled(payload.enabled)
    except WindowsIntegrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit("system.startup_changed", {"enabled": result["enabled"]})
    return {"updated": True, "startup": result}


@router.post("/api/v1/control/system/setup")
def control_setup(payload: SetupUpdate) -> dict:
    result = set_first_run_complete(payload.complete)
    _audit("system.setup_changed", {"complete": result["complete"]})
    return {"updated": True, "setup": result}


@router.post("/api/v1/control/system/open-data-folder")
def control_open_data_folder() -> dict:
    try:
        open_folder(settings.data_dir)
    except WindowsIntegrationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit("system.data_folder_opened")
    return {"opened": True, "path": str(settings.data_dir)}


@router.post("/api/v1/control/system/restart")
def control_restart() -> dict:
    if not request_runtime_command("restart"):
        raise HTTPException(status_code=503, detail="Runtime restart control is unavailable in this launch mode")
    _audit("system.restart_requested")
    return {"accepted": True, "command": "restart"}


@router.post("/api/v1/control/system/shutdown")
def control_shutdown() -> dict:
    if not request_runtime_command("shutdown"):
        raise HTTPException(status_code=503, detail="Runtime shutdown control is unavailable in this launch mode")
    _audit("system.shutdown_requested")
    return {"accepted": True, "command": "shutdown"}

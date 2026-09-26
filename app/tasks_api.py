from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .main import require
from .services import approvals, task_calendar_continuity as continuity
from .services.tasks import (
    TaskError,
    create_task,
    delete_task,
    list_notifications,
    list_tasks,
    mark_notification,
    scheduler,
    update_task,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
UI_DIR = ROOT_DIR / "ui"


@asynccontextmanager
async def task_lifespan(_):
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


router = APIRouter(lifespan=task_lifespan)


class TaskCreate(BaseModel):
    mutation_id: str | None = Field(default=None, min_length=8, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=20000)
    status: str = "pending"
    priority: str = "normal"
    due_at: str | None = None
    remind_at: str | None = None
    recurrence: str = "none"
    recurrence_interval: int = Field(default=1, ge=1, le=365)
    contact_id: int | None = Field(default=None, ge=1)


class TaskUpdate(BaseModel):
    canonical_id: str | None = Field(default=None, min_length=45, max_length=45)
    mutation_id: str | None = Field(default=None, min_length=8, max_length=128)
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = None
    priority: str | None = None
    due_at: str | None = None
    remind_at: str | None = None
    recurrence: str | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)
    contact_id: int | None = Field(default=None, ge=1)


class NotificationUpdate(BaseModel):
    read: bool | None = None
    dismissed: bool | None = None


def _task_payload(model: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    return model.model_dump(exclude_unset=exclude_unset)


def _raise(exc: TaskError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/tasks", include_in_schema=False)
def tasks_workspace():
    page = UI_DIR / "tasks.html"
    if not page.exists():
        raise HTTPException(status_code=503, detail="Tasks workspace assets are unavailable")
    return FileResponse(page)


@router.get("/api/v1/tasks")
def client_tasks(
    status: str | None = Query(default=None, max_length=40),
    q: str = Query(default="", max_length=240),
    identity: dict = Depends(require("tasks.read")),
) -> dict:
    try:
        return {"items": continuity.list_federated_tasks(status=status, q=q, limit=250), "app": identity["app_key"]}
    except TaskError as exc:
        _raise(exc)


@router.post("/api/v1/tasks")
def client_task_create(payload: TaskCreate, identity: dict = Depends(require("tasks.write"))) -> dict:
    try:
        request = approvals.create_task_create_request(f"app:{identity['app_key']}", _task_payload(payload), owner=False)
        return {**request, "approval_required": True}
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.patch("/api/v1/tasks/{task_id}")
def client_task_update(task_id: int, payload: TaskUpdate, identity: dict = Depends(require("tasks.write"))) -> dict:
    try:
        arguments = _task_payload(payload, exclude_unset=True)
        canonical = arguments.pop("canonical_id", None)
        if not canonical:
            rows = continuity.list_federated_tasks(limit=500)
            match = next((row for row in rows if int(row.get("id") or 0) == int(task_id)), None)
            canonical = None if match is None else match.get("canonical_id")
        arguments["canonical_id"] = canonical
        request = approvals.create_task_update_request(f"app:{identity['app_key']}", arguments, owner=False)
        return {**request, "approval_required": True}
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/api/v1/tasks/{task_id}")
def client_task_delete(task_id: int, mutation_id: str, expected_revision: str, identity: dict = Depends(require("tasks.write"))) -> dict:
    rows = continuity.list_federated_tasks(limit=500)
    match = next((row for row in rows if int(row.get("id") or 0) == int(task_id)), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        request = approvals.create_task_delete_request(
            f"app:{identity['app_key']}",
            {"canonical_id": match["canonical_id"], "mutation_id": mutation_id, "expected_revision": expected_revision},
            owner=False,
        )
        return {**request, "approval_required": True}
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/notifications")
def client_notifications(
    unread_only: bool = False,
    identity: dict = Depends(require("notifications.read")),
) -> dict:
    return {"items": list_notifications(unread_only=unread_only, include_dismissed=False, limit=200), "app": identity["app_key"]}


@router.get("/api/v1/control/tasks")
def control_tasks(status: str | None = Query(default=None, max_length=40), q: str = Query(default="", max_length=240)) -> dict:
    try:
        return {"items": list_tasks(status=status, q=q, limit=500)}
    except TaskError as exc:
        _raise(exc)


@router.post("/api/v1/control/tasks")
def control_task_create(payload: TaskCreate) -> dict:
    try:
        return {"task": create_task(_task_payload(payload), created_by_type="owner")}
    except TaskError as exc:
        _raise(exc)


@router.patch("/api/v1/control/tasks/{task_id}")
def control_task_update(task_id: int, payload: TaskUpdate) -> dict:
    try:
        return {"task": update_task(task_id, _task_payload(payload, exclude_unset=True), actor_type="owner")}
    except TaskError as exc:
        _raise(exc)


@router.delete("/api/v1/control/tasks/{task_id}")
def control_task_delete(task_id: int) -> dict:
    if not delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": True}


@router.get("/api/v1/control/task-notifications")
def control_task_notifications(unread_only: bool = False, include_dismissed: bool = False) -> dict:
    return {"items": list_notifications(unread_only=unread_only, include_dismissed=include_dismissed, limit=500)}


@router.patch("/api/v1/control/notifications/{notification_id}")
def control_notification_update(notification_id: int, payload: NotificationUpdate) -> dict:
    try:
        return {"notification": mark_notification(notification_id, read=payload.read, dismissed=payload.dismissed)}
    except TaskError as exc:
        _raise(exc)

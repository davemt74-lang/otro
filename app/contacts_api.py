from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .main import require
from .services import contacts, shared_agent_context

router = APIRouter()


class ContactPayload(BaseModel):
    display_name: str | None = Field(default=None, max_length=240)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    organization: str | None = Field(default=None, max_length=240)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    relationship: str | None = Field(default=None, max_length=160)
    notes: str = Field(default="", max_length=50000)


def _payload_dict(payload: ContactPayload) -> dict:
    return payload.model_dump()


@router.get("/api/v1/contacts")
def client_contacts(
    q: str = Query(default="", max_length=240),
    limit: int = Query(default=100, ge=1, le=250),
    identity: dict = Depends(require("contacts.read")),
) -> dict:
    try:
        items = contacts.list_federated_contacts(q, limit)
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"items": items, "app": identity["app_key"], "query": q.strip()}


@router.get("/api/v1/control/contacts")
def control_contacts(
    q: str = Query(default="", max_length=240),
    limit: int = Query(default=250, ge=1, le=500),
) -> dict:
    try:
        local_items = contacts.list_federated_contacts(q, limit)
        cloud_items = []
        remaining = max(0, min(500, int(limit)) - len(local_items))
        if remaining:
            for row in shared_agent_context.cloud_candidates("contacts", q, min(remaining, 100)):
                key = str(row.get("key") or row.get("authority_key") or "")
                if "agent:" in key:
                    continue
                cloud_items.append({
                    "id": row.get("id"),
                    "display_name": row.get("title") or "VP3 Cloud contact",
                    "first_name": None,
                    "last_name": None,
                    "organization": None,
                    "email": None,
                    "phone": None,
                    "relationship": "VP3 Cloud",
                    "notes": row.get("content") or "",
                    "created_at": None,
                    "updated_at": row.get("updated_at"),
                    "authority_source": "vp3_cloud",
                    "authority_key": row.get("authority_key") or row.get("key"),
                    "canonical_id": row.get("canonical_id"),
                    "record_revision": row.get("record_revision"),
                    "federation_version": row.get("federation_version") or "2.4",
                    "mirror_only": True,
                    "read_only": True,
                    "source_label": "VP3 Cloud",
                })
        return {
            "items": local_items + cloud_items,
            "query": q.strip(),
            "sources": {"homeserver": len(local_items), "vp3_cloud": len(cloud_items)},
        }
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/control/contacts")
def control_contact_create(payload: ContactPayload) -> dict:
    try:
        return {"contact": contacts.create_contact(_payload_dict(payload)), "created": True}
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/api/v1/control/contacts/{contact_id}")
def control_contact_update(contact_id: int, payload: ContactPayload) -> dict:
    try:
        return {"contact": contacts.update_contact(contact_id, _payload_dict(payload)), "updated": True}
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/api/v1/control/contacts/{contact_id}")
def control_contact_delete(contact_id: int) -> dict:
    if not contacts.delete_contact(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"deleted": True}

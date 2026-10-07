from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .main import require
from .services import contacts, native_workspaces

router = APIRouter()


class ContactPayload(BaseModel):
    mutation_id: str | None = Field(default=None, min_length=8, max_length=128)
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)
    display_name: str | None = Field(default=None, max_length=240)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    organization: str | None = Field(default=None, max_length=240)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    relationship: str | None = Field(default=None, max_length=160)
    notes: str = Field(default="", max_length=50000)


def _payload_dict(payload: ContactPayload) -> dict:
    return payload.model_dump(exclude={"mutation_id", "expected_revision"})


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
    cloud_offset: int = Query(default=0,ge=0),
) -> dict:
    try:
        local_items = contacts.list_federated_contacts(q, limit)
        native = native_workspaces.items('contacts',q,offset=cloud_offset,limit=limit)
        cloud_items = [{**row,'display_name':row.get('display_name') or row['title'],
                        'organization':row.get('organization') or row.get('company'),
                        'notes':row['content']} for row in native['items']]
        return {
            "items": local_items + cloud_items,
            "query": q.strip(),
            "sources": {"homeserver": len(local_items), "vp3_cloud": len(cloud_items)},
            "cloud_count": native['count'], "synced_at":native['synced_at'],
        }
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/control/contacts")
def control_contact_create(payload: ContactPayload) -> dict:
    try:
        item = contacts.create_federated_contact({**_payload_dict(payload), "mutation_id": payload.mutation_id}, source_app_key="owner") if payload.mutation_id else contacts.create_contact(_payload_dict(payload))
        return {"contact": item, "created": True}
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/api/v1/control/contacts/{contact_id}")
def control_contact_update(contact_id: int, payload: ContactPayload) -> dict:
    try:
        return {"contact": contacts.update_contact(contact_id, _payload_dict(payload), expected_revision=payload.expected_revision), "updated": True}
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/api/v1/control/contacts/{contact_id}")
def control_contact_delete(contact_id: int, expected_revision: str | None = Query(default=None, min_length=64, max_length=64)) -> dict:
    try:
        if not contacts.delete_contact(contact_id, expected_revision=expected_revision):
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"deleted": True}
    except contacts.ContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

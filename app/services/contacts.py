from __future__ import annotations

from typing import Any

from ..database import db


class ContactError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _clean(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ContactError(f"Contact field exceeds {max_length} characters.")
    return text


def normalize_contact(payload: dict[str, Any]) -> dict[str, Any]:
    display_name = _clean(payload.get("display_name"), 240)
    first_name = _clean(payload.get("first_name"), 120)
    last_name = _clean(payload.get("last_name"), 120)
    organization = _clean(payload.get("organization"), 240)
    email = _clean(payload.get("email"), 320)
    phone = _clean(payload.get("phone"), 80)
    relationship = _clean(payload.get("relationship"), 160)
    notes = str(payload.get("notes") or "").strip()
    if len(notes) > 50000:
        raise ContactError("Contact notes exceed 50,000 characters.")

    if not display_name:
        display_name = " ".join(part for part in (first_name, last_name) if part).strip() or organization or email
    if not display_name:
        raise ContactError("Contact display_name is required.")

    return {
        "display_name": display_name,
        "first_name": first_name,
        "last_name": last_name,
        "organization": organization,
        "email": email,
        "phone": phone,
        "relationship": relationship,
        "notes": notes,
    }


def list_contacts(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    q = str(query or "").strip()
    if len(q) > 240:
        raise ContactError("Contact search exceeds 240 characters.")

    with db() as connection:
        if not q:
            rows = connection.execute(
                """
                SELECT id, display_name, first_name, last_name, organization, email, phone,
                       relationship, notes, created_at, updated_at
                FROM contacts
                ORDER BY display_name COLLATE NOCASE, id
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        else:
            pattern = f"%{q.lower()}%"
            rows = connection.execute(
                """
                SELECT id, display_name, first_name, last_name, organization, email, phone,
                       relationship, notes, created_at, updated_at
                FROM contacts
                WHERE lower(display_name) LIKE ?
                   OR lower(COALESCE(first_name,'')) LIKE ?
                   OR lower(COALESCE(last_name,'')) LIKE ?
                   OR lower(COALESCE(organization,'')) LIKE ?
                   OR lower(COALESCE(email,'')) LIKE ?
                   OR lower(COALESCE(phone,'')) LIKE ?
                   OR lower(COALESCE(relationship,'')) LIKE ?
                   OR lower(COALESCE(notes,'')) LIKE ?
                ORDER BY display_name COLLATE NOCASE, id
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, safe_limit),
            ).fetchall()
    return [dict(row) for row in rows]


def get_contact(contact_id: int) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, display_name, first_name, last_name, organization, email, phone,
                   relationship, notes, created_at, updated_at
            FROM contacts WHERE id=? LIMIT 1
            """,
            (int(contact_id),),
        ).fetchone()
    return dict(row) if row else None


def create_contact(payload: dict[str, Any]) -> dict[str, Any]:
    item = normalize_contact(payload)
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO contacts(display_name, first_name, last_name, organization, email, phone, relationship, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["display_name"], item["first_name"], item["last_name"], item["organization"],
                item["email"], item["phone"], item["relationship"], item["notes"],
            ),
        )
        contact_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'contact.created', 'contact', ?, '{}')
            """,
            (str(contact_id),),
        )
    return get_contact(contact_id) or {"id": contact_id, **item}


def update_contact(contact_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    item = normalize_contact(payload)
    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE contacts
            SET display_name=?, first_name=?, last_name=?, organization=?, email=?, phone=?,
                relationship=?, notes=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                item["display_name"], item["first_name"], item["last_name"], item["organization"],
                item["email"], item["phone"], item["relationship"], item["notes"], int(contact_id),
            ),
        )
        if cursor.rowcount != 1:
            raise ContactError("Contact not found.", 404)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'contact.updated', 'contact', ?, '{}')
            """,
            (str(contact_id),),
        )
    return get_contact(contact_id) or {"id": contact_id, **item}


def delete_contact(contact_id: int) -> bool:
    with db() as connection:
        cursor = connection.execute("DELETE FROM contacts WHERE id=?", (int(contact_id),))
        if cursor.rowcount != 1:
            return False
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'contact.deleted', 'contact', ?, '{}')
            """,
            (str(contact_id),),
        )
    return True

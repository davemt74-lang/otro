from __future__ import annotations

from typing import Any

from ..database import db
from . import federated_data


class ContactError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code

CONTACT_FIELDS = (
    "display_name", "first_name", "last_name", "organization",
    "email", "phone", "relationship", "notes",
)

MUTATION_ID_MAX = 128


def _mutation_id(value: Any) -> str:
    mutation_id = str(value or "").strip()
    if len(mutation_id) < 8 or len(mutation_id) > MUTATION_ID_MAX:
        raise ContactError("Contact mutation_id must be 8 to 128 characters.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(ch not in allowed for ch in mutation_id):
        raise ContactError("Contact mutation_id contains unsupported characters.")
    return mutation_id


def _expected_revision(value: Any) -> str:
    revision = str(value or "").strip().lower()
    if len(revision) != 64 or any(ch not in "0123456789abcdef" for ch in revision):
        raise ContactError("Contact expected_revision must be a 64-character SHA-256 value.")
    return revision


def _canonical(value: Any) -> str:
    canonical = str(value or "").strip().lower()
    if len(canonical) != 45 or not canonical.startswith("fd24_"):
        raise ContactError("HomeServer contact canonical_id is invalid.")
    suffix = canonical[5:]
    if any(ch not in "0123456789abcdef" for ch in suffix):
        raise ContactError("HomeServer contact canonical_id is invalid.")
    return canonical


def normalize_contact_create_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    unknown = set(raw) - {"mutation_id", *CONTACT_FIELDS}
    if unknown:
        raise ContactError(f"Unsupported contacts.create argument: {sorted(unknown)[0]}")
    mutation_id = _mutation_id(raw.get("mutation_id"))
    contact = normalize_contact({key: raw.get(key) for key in CONTACT_FIELDS if key in raw})
    return {"mutation_id": mutation_id, **contact}


def normalize_contact_update_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    unknown = set(raw) - {"canonical_id", "mutation_id", "expected_revision", *CONTACT_FIELDS}
    if unknown:
        raise ContactError(f"Unsupported contacts.update argument: {sorted(unknown)[0]}")
    canonical = _canonical(raw.get("canonical_id"))
    mutation_id = _mutation_id(raw.get("mutation_id"))
    expected_revision = _expected_revision(raw.get("expected_revision"))
    fields = {key: raw[key] for key in CONTACT_FIELDS if key in raw}
    if not fields:
        raise ContactError("contacts.update requires at least one contact field.")
    for key, value in fields.items():
        if key == "notes":
            if len(str(value or "")) > 50000:
                raise ContactError("Contact notes exceed 50,000 characters.")
            continue
        limits = {
            "display_name": 240, "first_name": 120, "last_name": 120,
            "organization": 240, "email": 320, "phone": 80, "relationship": 160,
        }
        _clean(value, limits[key])
    return {
        "canonical_id": canonical,
        "mutation_id": mutation_id,
        "expected_revision": expected_revision,
        **fields,
    }


def normalize_contact_delete_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    unknown = set(raw) - {"canonical_id", "mutation_id", "expected_revision"}
    if unknown:
        raise ContactError(f"Unsupported contacts.delete argument: {sorted(unknown)[0]}")
    return {
        "canonical_id": _canonical(raw.get("canonical_id")),
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _expected_revision(raw.get("expected_revision")),
    }


def safe_contact_mutation_meta(action: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    canonical = str(raw.get("canonical_id") or "")
    mutation_id = str(raw.get("mutation_id") or "")
    expected_revision = str(raw.get("expected_revision") or "")
    fields = sorted(key for key in CONTACT_FIELDS if key in raw)
    return {
        "action": str(action or "")[:32],
        "canonical_id_present": bool(canonical),
        "canonical_id_prefix": canonical[:5] if canonical else "",
        "mutation_id_present": bool(mutation_id),
        "expected_revision_present": bool(expected_revision),
        "field_names": fields,
        "field_count": len(fields),
        "has_notes": "notes" in raw,
    }



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


def _like_pattern(query: str) -> str:
    escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for value in query.split():
        token = value.strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens[:16]


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
            tokens = _search_tokens(q) or [q]
            search_text = """
                lower(
                    COALESCE(display_name,'') || ' ' ||
                    COALESCE(first_name,'') || ' ' ||
                    COALESCE(last_name,'') || ' ' ||
                    COALESCE(organization,'') || ' ' ||
                    COALESCE(email,'') || ' ' ||
                    COALESCE(phone,'') || ' ' ||
                    COALESCE(relationship,'') || ' ' ||
                    COALESCE(notes,'')
                )
            """
            where = " AND ".join(f"({search_text}) LIKE ? ESCAPE '\\'" for _ in tokens)
            params = [_like_pattern(token) for token in tokens]
            params.append(safe_limit)
            rows = connection.execute(
                f"""
                SELECT id, display_name, first_name, last_name, organization, email, phone,
                       relationship, notes, created_at, updated_at
                FROM contacts
                WHERE {where}
                ORDER BY display_name COLLATE NOCASE, id
                LIMIT ?
                """,
                params,
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



def _authority_key(contact_id: int) -> str:
    if int(contact_id) < 1:
        raise ContactError("Contact identity is invalid.", 500)
    return f"address_book:{int(contact_id)}"


def _contact_id_from_canonical(canonical_id_value: str) -> int:
    key = federated_data.resolve_authority_key(
        canonical_id_value,
        authority_source="homeserver",
        dataset="contacts",
        observed_source="homeserver",
    )
    if not key or not key.startswith("address_book:"):
        raise ContactError("HomeServer contact not found.", 404)
    raw = key.split(":", 1)[1]
    try:
        contact_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ContactError("HomeServer contact identity is invalid.", 404) from exc
    if contact_id < 1:
        raise ContactError("HomeServer contact identity is invalid.", 404)
    expected = federated_data.canonical_id("homeserver", "contacts", _authority_key(contact_id))
    if expected != str(canonical_id_value or "").strip():
        raise ContactError("HomeServer contact identity does not match its authority.", 409)
    return contact_id


def federated_contact(record: dict[str, Any]) -> dict[str, Any]:
    contact_id = int(record.get("id") or 0)
    key = _authority_key(contact_id)
    content = " · ".join(
        value for value in (
            str(record.get("organization") or "").strip(),
            str(record.get("relationship") or "").strip(),
            str(record.get("email") or "").strip(),
            str(record.get("phone") or "").strip(),
            str(record.get("notes") or "").strip(),
        ) if value
    )
    envelope = federated_data.envelope(
        "homeserver",
        "contacts",
        key,
        title=str(record.get("display_name") or "Contact"),
        content=content,
        updated_at=str(record.get("updated_at") or record.get("created_at") or ""),
    )
    federated_data.observe(envelope, observed_source="homeserver")
    out = dict(record)
    out.update({
        "contact_class": "address_book",
        "authority_source": "homeserver",
        "authority_key": key,
        "canonical_id": envelope["canonical_id"],
        "record_revision": envelope["record_revision"],
        "federation_version": envelope["federation_version"],
        "mirror_only": False,
        "read_only": False,
        "source_label": "HomeServer",
        "allowed_mutations": ["update", "delete"],
    })
    return out


def list_federated_contacts(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    return [federated_contact(row) for row in list_contacts(query, limit)]


def get_federated_contact(contact_id: int) -> dict[str, Any] | None:
    row = get_contact(contact_id)
    return federated_contact(row) if row else None


def get_federated_contact_by_canonical(canonical_id_value: str) -> dict[str, Any] | None:
    try:
        contact_id = _contact_id_from_canonical(canonical_id_value)
    except ContactError as exc:
        if exc.status_code == 404:
            return None
        raise
    row = get_contact(contact_id)
    return federated_contact(row) if row else None


def _mutation_hash(action_key: str, payload: dict[str, Any]) -> str:
    import hashlib
    import json
    encoded = json.dumps(
        {"action": action_key, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mutation_replay(
    source_app_key: str,
    mutation_id: str,
    action_key: str,
    request_hash: str,
) -> dict[str, Any] | None:
    import json
    with db() as connection:
        row = connection.execute(
            """
            SELECT action_key,request_hash,canonical_id,result_json
            FROM federated_contact_mutations
            WHERE source_app_key=? AND mutation_id=? LIMIT 1
            """,
            (source_app_key, mutation_id),
        ).fetchone()
    if row is None:
        return None
    if str(row["action_key"]) != action_key or str(row["request_hash"]) != request_hash:
        raise ContactError("Contact mutation_id was already used with different arguments.", 409)
    try:
        result = json.loads(row["result_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    result["idempotent_replay"] = True
    return result


def _record_mutation(
    source_app_key: str,
    mutation_id: str,
    action_key: str,
    request_hash: str,
    canonical_id: str | None,
    result: dict[str, Any],
) -> None:
    import json
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_contact_mutations(
                source_app_key,mutation_id,action_key,request_hash,canonical_id,result_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                source_app_key,
                mutation_id,
                action_key,
                request_hash,
                canonical_id,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def _assert_expected_revision(item: dict[str, Any], expected_revision: str) -> None:
    current = str(item.get("record_revision") or "").strip().lower()
    if not current or current != expected_revision:
        raise ContactError("HomeServer contact changed after this edit was prepared. Refresh and try again.", 409)


def create_federated_contact(
    payload: dict[str, Any],
    *,
    source_app_key: str = "owner",
) -> dict[str, Any]:
    normalized = normalize_contact_create_arguments(payload)
    mutation_id = str(normalized.pop("mutation_id"))
    request_hash = _mutation_hash("contacts.create", normalized)
    replay = _mutation_replay(source_app_key, mutation_id, "contacts.create", request_hash)
    if replay is not None:
        contact = replay.get("contact")
        if isinstance(contact, dict):
            return contact
        raise ContactError("Stored contact mutation result is unavailable.", 500)
    item = federated_contact(create_contact(normalized))
    _record_mutation(
        source_app_key, mutation_id, "contacts.create", request_hash,
        str(item.get("canonical_id") or ""), {"contact": item},
    )
    return item


def update_federated_contact(
    canonical_id_value: str,
    payload: dict[str, Any],
    *,
    source_app_key: str = "owner",
) -> dict[str, Any]:
    normalized = normalize_contact_update_arguments({"canonical_id": canonical_id_value, **payload})
    canonical = str(normalized.pop("canonical_id"))
    mutation_id = str(normalized.pop("mutation_id"))
    expected_revision = str(normalized.pop("expected_revision"))
    request_hash = _mutation_hash(
        "contacts.update",
        {"canonical_id": canonical, "expected_revision": expected_revision, **normalized},
    )
    replay = _mutation_replay(source_app_key, mutation_id, "contacts.update", request_hash)
    if replay is not None:
        contact = replay.get("contact")
        if isinstance(contact, dict):
            return contact
        raise ContactError("Stored contact mutation result is unavailable.", 500)
    contact_id = _contact_id_from_canonical(canonical)
    current = get_federated_contact(contact_id)
    if current is None:
        raise ContactError("HomeServer contact not found.", 404)
    _assert_expected_revision(current, expected_revision)
    merged = {
        key: normalized[key] if key in normalized else current.get(key)
        for key in CONTACT_FIELDS
    }
    item = federated_contact(update_contact(contact_id, merged))
    _record_mutation(
        source_app_key, mutation_id, "contacts.update", request_hash,
        canonical, {"contact": item},
    )
    return item


def delete_federated_contact(
    canonical_id_value: str,
    *,
    mutation_id: str,
    expected_revision: str,
    source_app_key: str = "owner",
) -> bool:
    canonical = _canonical(canonical_id_value)
    mutation_id = _mutation_id(mutation_id)
    expected_revision = _expected_revision(expected_revision)
    request_hash = _mutation_hash(
        "contacts.delete",
        {"canonical_id": canonical, "expected_revision": expected_revision},
    )
    replay = _mutation_replay(source_app_key, mutation_id, "contacts.delete", request_hash)
    if replay is not None:
        return bool(replay.get("deleted"))
    contact_id = _contact_id_from_canonical(canonical)
    current = get_federated_contact(contact_id)
    if current is None:
        raise ContactError("HomeServer contact not found.", 404)
    _assert_expected_revision(current, expected_revision)
    key = _authority_key(contact_id)
    if not delete_contact(contact_id):
        return False
    federated_data.mark_tombstone(
        "homeserver",
        "contacts",
        key,
        observed_source="homeserver",
    )
    _record_mutation(
        source_app_key, mutation_id, "contacts.delete", request_hash,
        canonical, {"deleted": True, "canonical_id": canonical},
    )
    return True

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v242-knowledge-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import federated_data, knowledge, knowledge_collection_policy, pairing  # noqa: E402

    initialize_database()

    created = knowledge.create_knowledge_item(
        "Local operating note",
        "note",
        "Phoenix launch checklist and operational reference.",
        None,
    )
    item_id = int(created["id"])
    assert item_id > 0

    request = pairing.create_pairing_request("vp3", "VP3", ["knowledge.search"])
    approved = pairing.approve_pairing_request(request["request_id"])
    assert approved is not None
    with db() as connection:
        app = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key='vp3' LIMIT 1"
        ).fetchone()
    assert app is not None

    identity = {
        "id": int(app["id"]),
        "app_key": "vp3",
        "permissions": ["knowledge.search"],
        "scope": {},
    }
    result = knowledge_collection_policy.scoped_search(identity, "Phoenix", limit=10)
    assert result["count"] >= 1
    row = next(item for item in result["items"] if int(item["id"]) == item_id)

    assert row["authority_source"] == "homeserver"
    assert row["authority_key"] == f"knowledge_item:{item_id}"
    assert row["canonical_id"] == federated_data.canonical_id(
        "homeserver", "knowledge", row["authority_key"]
    )
    assert row["canonical_id"].startswith("fd24_")
    assert row["federation_version"] == "2.4"
    assert row["mirror_only"] is False
    assert len(row["record_revision"]) == 64
    assert row["citation"]["uri"].startswith("homeserver://knowledge/")
    assert "source_path" not in row
    assert "absolute_path" not in row
    assert result["privacy"]["absolute_paths_exposed"] is False
    assert result["privacy"]["full_documents_returned"] is False

    recent = knowledge_collection_policy.scoped_search(identity, "", limit=10)
    recent_row = next(item for item in recent["items"] if int(item["id"]) == item_id)
    assert recent_row["canonical_id"] == row["canonical_id"]
    assert recent_row["authority_key"] == row["authority_key"]

print("HomeServer v2.4 Section 3 knowledge continuity: PASS")

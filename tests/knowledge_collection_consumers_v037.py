from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-consumers-v037-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    travel_dir = root / "travel"
    private_dir = root / "private"
    travel_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)
    (travel_dir / "travel.txt").write_text(
        "COLLECTION_CONSUMER_COMMON_3706 TRAVEL_VISIBLE_3707 local travel context",
        encoding="utf-8",
    )
    (private_dir / "private.txt").write_text(
        "COLLECTION_CONSUMER_COMMON_3706 PRIVATE_HIDDEN_3708 local private context",
        encoding="utf-8",
    )
    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import app_collaboration, app_scopes, context_chat, context_engine, tools  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}
        ).status_code == 200

        for key, name in (("travel", "Travel"), ("private", "Private")):
            created = client.post(
                "/api/v1/control/knowledge/collections",
                json={"collection_key": key, "name": name},
            )
            assert created.status_code == 200, created.text

        source_ids: dict[str, int] = {}
        for key, folder in (("travel", travel_dir), ("private", private_dir)):
            source = client.post(
                "/api/v1/control/knowledge/sources",
                json={"path": str(folder), "label": key.title(), "scan_interval_seconds": 60},
            )
            assert source.status_code == 200, source.text
            source_ids[key] = int(source.json()["source"]["id"])
            assigned = client.put(
                f"/api/v1/control/knowledge/sources/{source_ids[key]}/collection",
                json={"collection_key": key},
            )
            assert assigned.status_code == 200, assigned.text

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "collection-consumer-v037",
                "app_name": "Collection Consumer",
                "permissions": ["knowledge.search", "tools.execute"],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": pair["code"]}
        ).status_code == 200
        auth = {"Authorization": f"Bearer {pair['claim_token']}"}
        app_row = next(
            item for item in client.get("/api/v1/control/apps").json()["apps"]
            if item["app_key"] == "collection-consumer-v037"
        )
        app_id = int(app_row["id"])
        restricted = client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["travel"]},
        )
        assert restricted.status_code == 200, restricted.text

        # Direct paired search: private collection is invisible.
        hidden = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "PRIVATE_HIDDEN_3708"},
            headers=auth,
        )
        assert hidden.status_code == 200
        assert hidden.json()["count"] == 0

        # Agent context: filter the retrieved bundle before it reaches the model
        # prompt or context-retrieval ledger.
        scope = app_scopes.get_scope(app_id)
        bundle = context_engine.ContextBundle(
            memory=[],
            knowledge=[],
            contacts=[],
            sources=[],
            context_chars=0,
            settings={"max_context_chars": 12000},
        )
        raw = context_engine._knowledge_candidates("COLLECTION_CONSUMER_COMMON_3706", limit=8)
        assert len(raw) >= 2
        bundle.knowledge = [
            {
                "id": int(item["id"]),
                "title": str(item.get("title") or ""),
                "content": str(item.get("snippet") or item.get("content") or ""),
                "kind": str(item.get("kind") or ""),
                "updated_at": item.get("updated_at"),
            }
            for item in raw
        ]
        bundle.sources = [
            {"kind": "knowledge", "id": item["id"], "title": item["title"]}
            for item in bundle.knowledge
        ]
        bundle.context_chars = sum(len(item["content"]) for item in bundle.knowledge)
        context_chat._apply_collection_scope_to_bundle(
            "app:collection-consumer-v037", scope, bundle, owner_tools=False
        )
        filtered_text = json.dumps(bundle.knowledge, ensure_ascii=False)
        assert "TRAVEL_VISIBLE_3707" in filtered_text
        assert "PRIVATE_HIDDEN_3708" not in filtered_text
        assert len(bundle.knowledge) == 1
        assert len(bundle.sources) == 1

        # Cross-wrapper collaboration consumes only the source app's allowed
        # collection, even when both collections match the same query.
        collaboration = app_collaboration.collect_context(
            1,
            "COLLECTION_CONSUMER_COMMON_3706",
            [
                {
                    "source_app_id": app_id,
                    "source_app_key": "collection-consumer-v037",
                    "source_name": "Collection Consumer",
                    "memory_allowed": False,
                    "knowledge_allowed": True,
                    "scope": scope,
                }
            ],
            max_chars=6000,
        )
        assert "TRAVEL_VISIBLE_3707" in collaboration["fragment"]
        assert "PRIVATE_HIDDEN_3708" not in collaboration["fragment"]
        assert collaboration["sources"][0]["knowledge_count"] == 1

        # Agent/tool search uses the same citation-safe collection projection.
        granted = {"knowledge.search", "tools.execute"}
        tool_hidden = tools.execute_tool(
            "app:collection-consumer-v037",
            "knowledge.search",
            {"query": "PRIVATE_HIDDEN_3708", "limit": 10},
            granted,
            owner=False,
        )
        assert tool_hidden["result"]["count"] == 0
        tool_visible = tools.execute_tool(
            "app:collection-consumer-v037",
            "knowledge.search",
            {"query": "TRAVEL_VISIBLE_3707", "limit": 10},
            granted,
            owner=False,
        )
        assert tool_visible["result"]["count"] == 1
        tool_item = tool_visible["result"]["items"][0]
        assert tool_item["citation"]["collection_key"] == "travel"
        assert "source_path" not in tool_item
        assert "content" not in tool_item

        # Deleting an in-use collection cannot erase the final scope row and
        # silently convert this app to unrestricted access.
        blocked_delete = client.delete("/api/v1/control/knowledge/collections/travel")
        assert blocked_delete.status_code == 409
        still_restricted = client.get(
            f"/api/v1/control/apps/{app_id}/knowledge-collections"
        ).json()
        assert still_restricted == {
            "restricted": True,
            "collections": ["travel"],
            "collection_names": ["Travel"],
        }

print("HomeServer knowledge collection consumers v0.37 regression passed")

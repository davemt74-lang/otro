from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import knowledge_collections_remote, remote_bridge  # noqa: E402

knowledge_collections_remote.install()
assert getattr(remote_bridge, "_knowledge_collections_v037_installed", False)
assert getattr(remote_bridge, "_knowledge_folder_mapping_v062_installed", False)

# Reinstalling either layer must remain idempotent.
before = remote_bridge.dispatch_remote_request
knowledge_collections_remote.install()
assert remote_bridge.dispatch_remote_request is before

fake_response = Mock()
fake_response.status_code = 200
fake_response.json.return_value = {"items": [], "privacy": {"local_paths_exposed": False}}
fake_client = Mock()
fake_client.__enter__ = Mock(return_value=fake_client)
fake_client.__exit__ = Mock(return_value=False)
fake_client.get.return_value = fake_response
fake_client.post.return_value = fake_response
fake_client.delete.return_value = fake_response

token = "x" * 32
with patch("app.services.knowledge_folder_mapping_remote.httpx.Client", return_value=fake_client):
    collections = remote_bridge.dispatch_remote_request("knowledge.collections.list", {}, token)
    folders = remote_bridge.dispatch_remote_request("knowledge.folders.list", {}, token)
    mapped = remote_bridge.dispatch_remote_request(
        "knowledge.folder.map",
        {"collection_key": "meetings", "label": "Meeting notes"},
        token,
    )
    written = remote_bridge.dispatch_remote_request(
        "knowledge.item.write",
        {
            "collection_key": "meetings",
            "title": "Summary",
            "kind": "summary",
            "content": "Synthetic summary",
        },
        token,
    )
    removed = remote_bridge.dispatch_remote_request(
        "knowledge.folder.unmap", {"mapping_id": "source-42"}, token
    )

for response in (collections, folders, mapped, written, removed):
    assert response["ok"] is True

get_paths = [call.args[0] for call in fake_client.get.call_args_list]
assert get_paths == [
    "/api/v1/knowledge/collections-v062",
    "/api/v1/knowledge/folder-mappings-v062",
]
post_paths = [call.args[0] for call in fake_client.post.call_args_list]
assert post_paths == [
    "/api/v1/knowledge/folder-mappings-v062",
    "/api/v1/knowledge/items-v062",
]
fake_client.delete.assert_called_once_with(
    "/api/v1/knowledge/folder-mappings-v062/source-42",
    headers={"Authorization": "Bearer " + token},
)

for call in fake_client.get.call_args_list + fake_client.post.call_args_list:
    assert call.kwargs["headers"]["Authorization"] == "Bearer " + token

try:
    remote_bridge.dispatch_remote_request(
        "knowledge.folder.unmap", {"mapping_id": "../../private"}, token
    )
except remote_bridge.RemoteBridgeError as exc:
    assert "valid mapping_id" in str(exc)
else:
    raise AssertionError("unsafe mapping id was accepted")

print("HomeServer native knowledge folder mapping v0.62 remote routing contract passed")

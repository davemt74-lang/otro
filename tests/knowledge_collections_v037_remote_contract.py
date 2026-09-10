from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import knowledge_collections_remote, remote_bridge  # noqa: E402

original = remote_bridge.dispatch_remote_request
knowledge_collections_remote.install()
wrapped = remote_bridge.dispatch_remote_request
assert wrapped is original or getattr(remote_bridge, "_knowledge_collections_v037_installed", False)

# Reinstalling must be idempotent and never stack duplicate wrappers.
before = remote_bridge.dispatch_remote_request
knowledge_collections_remote.install()
assert remote_bridge.dispatch_remote_request is before

fake_response = Mock()
fake_response.status_code = 200
fake_response.json.return_value = {"items": [], "citation_version": "v0.37"}
fake_client = Mock()
fake_client.__enter__ = Mock(return_value=fake_client)
fake_client.__exit__ = Mock(return_value=False)
fake_client.get.return_value = fake_response

with patch("app.services.knowledge_collections_remote.httpx.Client", return_value=fake_client):
    response = remote_bridge.dispatch_remote_request(
        "knowledge.search",
        {"query": "synthetic", "limit": 7},
        "x" * 32,
    )

assert response["ok"] is True
fake_client.get.assert_called_once()
args, kwargs = fake_client.get.call_args
assert args[0] == "/api/v1/knowledge/search-v037"
assert kwargs["params"] == {"q": "synthetic", "limit": 7}
assert kwargs["headers"]["Authorization"] == "Bearer " + ("x" * 32)

print("HomeServer knowledge collections v0.37 remote routing contract passed")

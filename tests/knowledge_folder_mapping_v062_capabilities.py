from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-v062-capabilities-") as temp_root:
    os.environ["HOMESERVER_DATA_DIR"] = str(Path(temp_root) / "data")

    from app.runtime import app  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        response = client.get("/api/v1/knowledge/capabilities-v062")
        assert response.status_code == 200, response.text
        capability = response.json()

        assert capability["version"] == "v0.62"
        assert capability["requires"] == {"knowledge_search": "v0.37"}
        assert capability["permissions"] == ["knowledge.search", "knowledge.write"]
        assert capability["operations"] == [
            "knowledge.collections.list",
            "knowledge.folders.list",
            "knowledge.folder.map",
            "knowledge.folder.unmap",
            "knowledge.item.write",
        ]
        assert capability["native_folder_picker"]["supported"] is (os.name == "nt")
        assert capability["native_folder_picker"]["runs_on_homeserver"] is True
        assert capability["native_folder_picker"]["caller_supplies_path"] is False
        assert capability["native_folder_picker"]["absolute_path_exposed"] is False
        assert capability["collection_scoped"] is True
        assert capability["knowledge_kind_scoped_writes"] is True
        assert capability["citation_safe_search"] is True

print("HomeServer native knowledge folder mapping v0.62 capability contract passed")

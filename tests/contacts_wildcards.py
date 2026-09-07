from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-contact-wildcards-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

    with TestClient(app) as client:
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.post(
            "/api/v1/control/contacts",
            json={"display_name": "Synthetic Percent Contact", "notes": "literal percent marker 50% complete"},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/contacts",
            json={"display_name": "Synthetic Under Score", "notes": "literal underscore marker alpha_beta"},
        ).status_code == 200

        percent = client.get("/api/v1/control/contacts?q=%25")
        assert percent.status_code == 200
        assert [item["display_name"] for item in percent.json()["items"]] == ["Synthetic Percent Contact"]

        underscore = client.get("/api/v1/control/contacts?q=_")
        assert underscore.status_code == 200
        assert [item["display_name"] for item in underscore.json()["items"]] == ["Synthetic Under Score"]

print("HomeServer contact wildcard test passed")

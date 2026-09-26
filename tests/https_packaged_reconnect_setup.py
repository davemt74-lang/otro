from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if not os.environ.get("HOMESERVER_DATA_DIR"):
    raise SystemExit("HOMESERVER_DATA_DIR is required")

config_path_raw = os.environ.get("HOMESERVER_HTTPS_RECONNECT_CONFIG")
if not config_path_raw:
    raise SystemExit("HOMESERVER_HTTPS_RECONNECT_CONFIG is required")

port = int(os.environ.get("HOMESERVER_HTTPS_RECONNECT_PORT", "48766"))
endpoint = f"http://127.0.0.1:{port}/poll"
session_token = "S" * 64

from app.database import initialize_database  # noqa: E402
from app.services.https_bridge_session import load_https_session, save_https_session  # noqa: E402
from app.services.pairing import approve_pairing_request, create_pairing_request  # noqa: E402
from app.services.remote_bridge import cloud_connection_status, get_bridge_settings, save_vp3_https_settings  # noqa: E402

initialize_database()

pair = create_pairing_request(
    "vp3",
    "VP3",
    ["memory.read", "knowledge.search", "contacts.read", "tasks.read", "events.read", "files.read", "notifications.read"],
)
approved = approve_pairing_request(pair["request_id"])
assert approved is not None
assert approved["app_key"] == "vp3"

local_token = str(pair["claim_token"])
assert len(local_token) >= 32

save_https_session(endpoint, session_token)
saved = load_https_session()
assert saved is not None
assert saved["endpoint"] == endpoint
assert saved["session_token"] == session_token

settings = save_vp3_https_settings(endpoint, True)
assert settings["transport"] == "vp3_https"
assert settings["enabled"] is True
assert settings["https_endpoint"] == endpoint
assert get_bridge_settings()["https_endpoint"] == endpoint

status = cloud_connection_status()
assert status["cloud"]["paired"] is True
assert status["cloud"]["connected"] is False
assert status["cloud"]["state"] in {"offline", "reconnecting"}

config_path = Path(config_path_raw)
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(
    json.dumps(
        {
            "endpoint": endpoint,
            "local_token": local_token,
            "session_token": session_token,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)

print("Packaged VP3 HTTPS reconnect fixture configured")

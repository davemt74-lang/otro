from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if not os.environ.get("HOMESERVER_DATA_DIR"):
    raise SystemExit("HOMESERVER_DATA_DIR is required")

token_file_raw = os.environ.get("HOMESERVER_REMOTE_TOKEN_FILE")
if not token_file_raw:
    raise SystemExit("HOMESERVER_REMOTE_TOKEN_FILE is required")

from app.database import db, initialize_database  # noqa: E402
from app.services.pairing import approve_pairing, create_pairing_request  # noqa: E402
from app.services.remote_bridge import save_bridge_settings  # noqa: E402

port = int(os.environ.get("HOMESERVER_REMOTE_TEST_PORT", "48765"))
initialize_database()
configured = save_bridge_settings(True, f"ws://127.0.0.1:{port}/relay")
assert configured["enabled"] is True

with db() as connection:
    connection.execute(
        "INSERT INTO agent_memory(memory_key, content, importance) VALUES (?, ?, ?)",
        ("remote-packaged", "PACKAGED_REMOTE_PRIVATE_MEMORY_58241", 0.9),
    )

pair = create_pairing_request(
    "remote-packaged",
    "Packaged Remote Test",
    ["memory.read"],
)
approved = approve_pairing(pair["code"])
assert approved is not None
assert approved["permissions"] == ["memory.read"]

token_file = Path(token_file_raw)
token_file.parent.mkdir(parents=True, exist_ok=True)
token_file.write_text(pair["claim_token"], encoding="utf-8")

print(f"Packaged remote bridge configured on loopback broker port {port}")

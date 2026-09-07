from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if not os.environ.get("HOMESERVER_DATA_DIR"):
    raise SystemExit("HOMESERVER_DATA_DIR is required")

from app.database import initialize_database  # noqa: E402
from app.services.remote_bridge import save_bridge_settings  # noqa: E402

port = int(os.environ.get("HOMESERVER_REMOTE_TEST_PORT", "48765"))
initialize_database()
configured = save_bridge_settings(True, f"ws://127.0.0.1:{port}/relay")
assert configured["enabled"] is True
print(f"Packaged remote bridge configured on loopback broker port {port}")

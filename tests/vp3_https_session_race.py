from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

with tempfile.TemporaryDirectory(prefix="homeserver-vp3-session-race-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.services.https_bridge_session import (
        clear_https_session_if_matches,
        https_session_matches,
        load_https_session,
        save_https_session,
    )

    endpoint = "https://vp3.me/api/homeserver-https-poll-v1300.php"
    old_token = "O" * 64
    new_token = "N" * 64

    save_https_session(endpoint, old_token)
    assert https_session_matches(old_token) is True

    # Simulate a replacement pairing being saved while the old worker still
    # has an in-flight request using the superseded token.
    save_https_session(endpoint, new_token)

    assert https_session_matches(old_token) is False
    assert https_session_matches(new_token) is True

    # The stale worker must not be able to erase the new pairing.
    assert clear_https_session_if_matches(old_token) is False
    current = load_https_session()
    assert current is not None
    assert current["session_token"] == new_token

    # The active token can still be explicitly cleared when required.
    assert clear_https_session_if_matches(new_token) is True
    assert load_https_session() is None

print("VP3 HTTPS session replacement race regression passed")

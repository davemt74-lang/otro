from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-scheduler-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.services.knowledge_sources import scheduler as knowledge_scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    assert knowledge_scheduler._thread is None
    with TestClient(app):
        assert knowledge_scheduler._thread is not None
        assert knowledge_scheduler._thread.is_alive()
        # Keep this regression isolated from reminder work after proving both
        # router lifespans coexist under the packaged application.
        task_scheduler.stop()

    assert knowledge_scheduler._thread is None

print("HomeServer Knowledge Sync scheduler lifespan test passed")

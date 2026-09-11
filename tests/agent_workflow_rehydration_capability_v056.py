from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-rehydration-capability-v056-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.text
        payload = response.json()
        capability = payload["agent_workflow_rehydration"]
        assert capability == {
            "version": "v0.56",
            "persistent_checkpoint": True,
            "idempotent": True,
            "drift_detection": True,
            "canonical_plan_run_state": True,
            "auto_execute": False,
            "explicit_actions_preserved": True,
            "paired_app_scoped": True,
            "requires_resume": "v0.55",
            "actions_via": "v0.53",
        }
        assert "agent.workflows.rehydration.v056" in payload["features"]
        assert "agent.chat.workflow_checkpoint.v056" in payload["features"]

print("HomeServer v0.56 Workflow Rehydration capability registry contract passed")

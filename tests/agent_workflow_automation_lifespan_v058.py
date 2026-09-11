from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-automation-lifespan-v058-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_workflow_automation_runtime  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    assert agent_workflow_automation_runtime.scheduler._thread is None
    with TestClient(app) as client:
        assert agent_workflow_automation_runtime.scheduler._thread is not None
        assert agent_workflow_automation_runtime.scheduler._thread.is_alive()
        task_scheduler.stop()

        denied = client.get("/api/v1/control/agent-workflows/automations/capability")
        assert denied.status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200
        capability = client.get("/api/v1/control/agent-workflows/automations/capability")
        assert capability.status_code == 200, capability.text
        payload = capability.json()
        assert payload["version"] == "v0.58"
        assert payload["trigger_types"] == ["once", "interval", "activity"]
        assert payload["requires_explicit_creation"] is True
        assert payload["durable_claim"] is True
        assert payload["durable_checkpoint_before_supervision"] is True
        assert payload["restart_resumable"] is True
        assert payload["stale_run_lease_seconds"] == 60
        assert payload["canonical_revalidation_at_fire"] is True
        assert payload["auto_approval"] is False
        assert payload["auto_retry"] is False
        assert payload["auto_parent_chat"] is False
        assert payload["uses_supervision"] == "v0.57"
        assert payload["uses_rehydration"] == "v0.56"

    assert agent_workflow_automation_runtime.scheduler._thread is None

print("HomeServer v0.58 workflow automation API/lifespan test passed")
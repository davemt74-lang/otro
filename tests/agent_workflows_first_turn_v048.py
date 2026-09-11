from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-first-turn-v048-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, agent_workflows, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    worker_id = 0

    def fake_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "parent-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "parent-model"}
            ],
        }

    def tool_names(tools) -> list[str]:
        return [str((item.get("function") or {}).get("name") or "") for item in (tools or [])]

    def fake_step(messages, *, tools, model_override=None):
        system = str(messages[0].get("content") or "")
        names = tool_names(tools)
        if "bounded specialist worker" in system:
            assert agent_workflows.MODEL_DELEGATE_TOOL_NAME not in names
            return {
                "content": "FIRST TURN WORKER RESULT",
                "provider": "openai",
                "model": model_override or "worker-model",
                "tool_calls": [],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
            }
        if any(item.get("role") == "tool" for item in messages):
            return {
                "content": "FIRST TURN PARENT RESULT",
                "provider": "openai",
                "model": model_override or "parent-model",
                "tool_calls": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
        assert agent_workflows.MODEL_DELEGATE_TOOL_NAME in names
        return {
            "content": "",
            "provider": "openai",
            "model": model_override or "parent-model",
            "tool_calls": [
                {
                    "id": "first-turn-v048",
                    "function": {
                        "name": agent_workflows.MODEL_DELEGATE_TOOL_NAME,
                        "arguments": {
                            "worker_agent_id": worker_id,
                            "task": "Handle the first-turn specialist task.",
                        },
                    },
                }
            ],
            "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
        }

    providers.inference_status = fake_status
    providers.generate_step = fake_step

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        created = client.post(
            "/api/v1/control/agents",
            json={"name": "First Turn Worker", "instructions": "Handle delegated work.", "model": "worker-model"},
        )
        assert created.status_code == 200, created.text
        worker_id = int(created.json()["agent"]["id"])

        policy = client.put(
            "/api/v1/control/agent-workflows/policy",
            json={"enabled": True, "max_context_chars": 12000},
        )
        assert policy.status_code == 200, policy.text
        agent_tools.save_policy(True, 3, False)

        response = client.post(
            "/api/v1/control/chat",
            json={"message": "Delegate this on the first turn."},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["reply"] == "FIRST TURN PARENT RESULT"
        conversation_id = payload["conversation_id"]
        assert conversation_id

        tasks = client.get(
            f"/api/v1/control/agent-workflows/delegations?conversation_id={conversation_id}"
        )
        assert tasks.status_code == 200, tasks.text
        items = tasks.json()["items"]
        assert len(items) == 1
        assert items[0]["conversation_id"] == conversation_id
        assert items[0]["worker_agent_id"] == worker_id
        assert items[0]["status"] == "completed"
        assert items[0]["metadata"]["invoked_by_model"] is True

print("HomeServer v0.48 first-turn delegation conversation binding regression passed")

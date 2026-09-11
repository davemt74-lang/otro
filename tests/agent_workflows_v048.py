from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflows-v048-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, agent_workflows, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    provider_calls: list[dict] = []
    phase = {"worker": "manual"}
    worker_id = 0

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "default-cloud-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "default-cloud-model"}
            ],
        }

    def _tool_names(tools) -> list[str]:
        return [str((item.get("function") or {}).get("name") or "") for item in (tools or [])]

    def _worker_assertions(system: str) -> None:
        assert "bounded specialist worker" in system
        assert "PARENT MEMORY MUST NOT BECOME WORKER MEMORY" not in system
        if phase["worker"] in {"manual", "model"}:
            assert "WORKER SPECIALIST MEMORY" in system
        elif phase["worker"] == "scoped":
            assert "WORKER SPECIALIST MEMORY" not in system

    def fake_generate(messages, model_override=None):
        system = str(messages[0].get("content") or "") if messages else ""
        provider_calls.append({"system": system, "tools": [], "model": model_override})
        if "bounded specialist worker" in system:
            _worker_assertions(system)
            result = {
                "manual": "MANUAL WORKER RESULT",
                "scoped": "SCOPED WRAPPER RESULT",
                "model": "SPECIALIST RESULT FROM WORKER",
            }[phase["worker"]]
            return {
                "content": result,
                "provider": "openai",
                "model": model_override or "default-cloud-model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            }
        return {
            "content": "FINAL WITHOUT TOOLS",
            "provider": "openai",
            "model": model_override or "default-cloud-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }

    def fake_generate_step(messages, *, tools, model_override=None):
        system = str(messages[0].get("content") or "") if messages else ""
        names = _tool_names(tools)
        provider_calls.append({"system": system, "tools": names, "model": model_override})

        if "bounded specialist worker" in system:
            _worker_assertions(system)
            assert agent_workflows.MODEL_DELEGATE_TOOL_NAME not in names
            return {
                "content": "SPECIALIST RESULT FROM WORKER",
                "provider": "openai",
                "model": model_override or "default-cloud-model",
                "tool_calls": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            }

        has_tool_result = any(item.get("role") == "tool" for item in messages)
        if not has_tool_result:
            assert agent_workflows.MODEL_DELEGATE_TOOL_NAME in names
            return {
                "content": "",
                "provider": "openai",
                "model": model_override or "default-cloud-model",
                "tool_calls": [
                    {
                        "id": "delegate-v048-1",
                        "function": {
                            "name": agent_workflows.MODEL_DELEGATE_TOOL_NAME,
                            "arguments": {
                                "worker_agent_id": worker_id,
                                "task": "Analyze the specialist memory and return a bounded result.",
                            },
                        },
                    }
                ],
                "usage": {"prompt_tokens": 15, "completion_tokens": 3, "total_tokens": 18},
            }
        return {
            "content": "PARENT USED SPECIALIST RESULT",
            "provider": "openai",
            "model": model_override or "default-cloud-model",
            "tool_calls": [],
            "usage": {"prompt_tokens": 18, "completion_tokens": 5, "total_tokens": 23},
        }

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate
    providers.generate_step = fake_generate_step

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        agents = client.get("/api/v1/control/agents").json()
        primary = next(item for item in agents["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        worker_response = client.post(
            "/api/v1/control/agents",
            json={
                "name": "Workflow Research Agent",
                "instructions": "Work as the specialist research persona.",
                "model": "worker-specialist-model",
            },
        )
        assert worker_response.status_code == 200, worker_response.text
        worker_id = int(worker_response.json()["agent"]["id"])

        disposable_response = client.post(
            "/api/v1/control/agents",
            json={"name": "Disposable Worker", "instructions": "Temporary.", "model": ""},
        )
        assert disposable_response.status_code == 200
        disposable_id = int(disposable_response.json()["agent"]["id"])

        with db() as connection:
            connection.execute(
                "INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, 'v048.parent', 'PARENT MEMORY MUST NOT BECOME WORKER MEMORY', 1)",
                (primary_id,),
            )
            connection.execute(
                "INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, 'v048.worker', 'WORKER SPECIALIST MEMORY', 1)",
                (worker_id,),
            )

        policy = client.get("/api/v1/control/agent-workflows/policy")
        assert policy.status_code == 200
        assert policy.json()["version"] == "v0.48"
        assert policy.json()["enabled"] is False

        same_agent = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": primary_id,
                "task": "Do not allow self delegation.",
            },
        )
        assert same_agent.status_code == 409

        agent_workflows.save_policy(False, 12000)
        agent_tools.save_policy(False, 3, False)
        parent_chat = client.post(
            "/api/v1/control/chat",
            json={"message": "Create a parent thread", "agent_id": primary_id},
        )
        assert parent_chat.status_code == 200, parent_chat.text
        conversation_id = parent_chat.json()["conversation_id"]

        queued = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Use the worker memory to answer this specialist task.",
                "conversation_id": conversation_id,
                "include_memory": True,
                "include_knowledge": True,
                "include_contacts": True,
                "cloud_allowed": True,
                "max_context_chars": 12000,
            },
        )
        assert queued.status_code == 200, queued.text
        queued_task = queued.json()
        assert queued_task["status"] == "queued"
        assert queued_task["parent_agent_id"] == primary_id
        assert queued_task["worker_agent_id"] == worker_id
        assert queued_task["conversation_id"] == conversation_id

        phase["worker"] = "manual"
        completed = client.post(f"/api/v1/control/agent-workflows/delegations/{queued_task['id']}/run")
        assert completed.status_code == 200, completed.text
        completed_task = completed.json()
        assert completed_task["status"] == "completed"
        assert completed_task["result"] == "MANUAL WORKER RESULT"
        assert completed_task["model"] == "worker-specialist-model"
        assert completed_task["agent_run_id"]
        assert completed_task["metadata"]["created_via"] == "owner_api"

        repeated = client.post(f"/api/v1/control/agent-workflows/delegations/{queued_task['id']}/run")
        assert repeated.status_code == 409

        filtered = client.get(
            f"/api/v1/control/agent-workflows/delegations?conversation_id={conversation_id}"
        )
        assert filtered.status_code == 200
        assert [item["id"] for item in filtered.json()["items"]] == [queued_task["id"]]

        disposable_task = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": disposable_id,
                "task": "This worker will be deleted before execution.",
            },
        )
        assert disposable_task.status_code == 200
        disposable_task_id = disposable_task.json()["id"]
        deleted = client.delete(f"/api/v1/control/agents/{disposable_id}")
        assert deleted.status_code == 200, deleted.text
        detached = client.get(f"/api/v1/control/agent-workflows/delegations/{disposable_task_id}").json()
        assert detached["worker_agent_id"] is None
        assert detached["worker_agent_name"] == "Disposable Worker"
        assert client.post(
            f"/api/v1/control/agent-workflows/delegations/{disposable_task_id}/run"
        ).status_code == 409

        app_key = "v048-wrapper"
        app_token = "v048-test-token-with-sufficient-length"
        token_hash = hashlib.sha256(app_token.encode("utf-8")).hexdigest()
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, ?, 'active', ?)",
                (app_key, "v0.48 Wrapper", token_hash),
            )
            app_id = int(cursor.lastrowid)
            for permission in ("agent.chat", "memory.read"):
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                    (app_id, permission),
                )
        headers = {"Authorization": f"Bearer {app_token}"}

        denied_create = client.post(
            "/api/v1/agent-workflows/delegations",
            headers=headers,
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Not granted yet.",
                "include_memory": True,
            },
        )
        assert denied_create.status_code == 403

        grant = client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_id}",
            json={"allowed": True},
        )
        assert grant.status_code == 200

        wrapper_task = client.post(
            "/api/v1/agent-workflows/delegations",
            headers=headers,
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Run with a permission snapshot that will be narrowed.",
                "include_memory": True,
            },
        )
        assert wrapper_task.status_code == 200, wrapper_task.text
        wrapper_task_id = wrapper_task.json()["id"]
        assert "memory.read" in wrapper_task.json()["permissions"]

        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND permission='memory.read'",
                (app_id,),
            )

        phase["worker"] = "scoped"
        scoped_run = client.post(
            f"/api/v1/agent-workflows/delegations/{wrapper_task_id}/run",
            headers=headers,
        )
        assert scoped_run.status_code == 200, scoped_run.text
        assert scoped_run.json()["result"] == "SCOPED WRAPPER RESULT"

        other_token = "v048-other-token-with-sufficient-length"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v048-other', 'Other Wrapper', 'active', ?)",
                (hashlib.sha256(other_token.encode("utf-8")).hexdigest(),),
            )
            other_app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (other_app_id,),
            )
        assert client.get(
            f"/api/v1/agent-workflows/delegations/{wrapper_task_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        ).status_code == 404

        revocable = client.post(
            "/api/v1/agent-workflows/delegations",
            headers=headers,
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Should be blocked after worker grant revocation.",
            },
        )
        assert revocable.status_code == 200
        revocable_id = revocable.json()["id"]
        assert client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_id}",
            json={"allowed": False},
        ).status_code == 200
        revoked_run = client.post(
            f"/api/v1/agent-workflows/delegations/{revocable_id}/run",
            headers=headers,
        )
        assert revoked_run.status_code == 403
        assert client.get(
            f"/api/v1/agent-workflows/delegations/{revocable_id}", headers=headers
        ).json()["status"] == "queued"

        assert client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_id}",
            json={"allowed": True},
        ).status_code == 200

        assert client.put(
            "/api/v1/control/agent-workflows/policy",
            json={"enabled": True, "max_context_chars": 12000},
        ).status_code == 200
        agent_tools.save_policy(True, 3, False)
        phase["worker"] = "model"
        model_chat = client.post(
            "/api/v1/control/chat",
            json={"message": "Ask the Research Agent to analyze this.", "agent_id": primary_id},
        )
        assert model_chat.status_code == 200, model_chat.text
        model_payload = model_chat.json()
        assert model_payload["reply"] == "PARENT USED SPECIALIST RESULT"
        assert model_payload["tools"]["call_count"] == 1
        model_tasks = client.get("/api/v1/control/agent-workflows/delegations?limit=10").json()["items"]
        model_task = next(item for item in model_tasks if item["metadata"].get("invoked_by_model"))
        assert model_task["status"] == "completed"
        assert model_task["worker_agent_id"] == worker_id
        assert model_task["result"] == "SPECIALIST RESULT FROM WORKER"
        assert any(
            agent_workflows.MODEL_DELEGATE_TOOL_NAME in call["tools"]
            and "bounded specialist worker" not in call["system"]
            for call in provider_calls
        )
        assert all(
            agent_workflows.MODEL_DELEGATE_TOOL_NAME not in call["tools"]
            for call in provider_calls
            if "bounded specialist worker" in call["system"]
        )

        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200
        caps = capabilities.json()
        assert caps["agent_workflows"]["version"] == "v0.48"
        assert caps["agent_workflows"]["persistent_tasks"] is True
        assert caps["agent_workflows"]["nested_delegation"] is False
        assert "agent.workflows.v048" in caps["features"]

        with db() as connection:
            completed_count = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_delegation_tasks WHERE status='completed'"
            ).fetchone()
            assert int(completed_count["count"]) >= 3
            audit_count = connection.execute(
                "SELECT COUNT(*) AS count FROM activity_log WHERE action='agent.delegation.completed'"
            ).fetchone()
            assert int(audit_count["count"]) >= 3

print("HomeServer v0.48 Agent Delegation & Multi-Agent Workflows regression passed")

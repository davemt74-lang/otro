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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-timeline-v050-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    observed_parent_systems: list[str] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "timeline-parent-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "timeline-parent-model"}
            ],
        }

    def fake_generate(messages, model_override=None):
        system = str(messages[0].get("content") or "") if messages else ""
        if "bounded specialist worker" in system:
            if "Timeline Specialist A" in system:
                result = "TIMELINE RESULT A: specialist A found the first bounded fact."
            elif "Timeline Specialist B" in system:
                result = "TIMELINE RESULT B: specialist B found the second bounded fact."
            else:
                result = "TIMELINE RESULT OTHER: another specialist result."
            return {
                "content": result,
                "provider": "openai",
                "model": model_override or "timeline-worker-model",
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        observed_parent_systems.append(system)
        if "Explicit specialist Agent result handoffs" in system:
            assert "TIMELINE RESULT A" in system
            assert "TIMELINE RESULT B" in system
            return {
                "content": "PARENT SYNTHESIZED A AND B",
                "provider": "openai",
                "model": model_override or "timeline-parent-model",
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            }
        return {
            "content": "PARENT BASELINE RESPONSE",
            "provider": "openai",
            "model": model_override or "timeline-parent-model",
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate

    with TestClient(app) as client:
        scheduler.stop()
        agent_tools.save_policy(False, 3, False)

        assert client.get("/api/v1/control/agent-workflows/timeline?conversation_id=missing").status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        agents = client.get("/api/v1/control/agents").json()["items"]
        primary = next(item for item in agents if item["is_primary"])
        primary_id = int(primary["id"])

        worker_ids: list[int] = []
        for name in ("Timeline Specialist A", "Timeline Specialist B", "Timeline Specialist C", "Timeline Specialist D"):
            response = client.post(
                "/api/v1/control/agents",
                json={
                    "name": name,
                    "instructions": f"Act only as {name} and return a bounded result.",
                    "model": "timeline-worker-model",
                },
            )
            assert response.status_code == 200, response.text
            worker_ids.append(int(response.json()["agent"]["id"]))

        bootstrap = client.post(
            "/api/v1/control/chat",
            json={"message": "Open the v0.50 workflow timeline thread", "agent_id": primary_id},
        )
        assert bootstrap.status_code == 200, bootstrap.text
        conversation_id = bootstrap.json()["conversation_id"]

        task_ids: list[int] = []
        for worker_id, brief in zip(
            worker_ids[:2],
            ("Find bounded fact A for parent synthesis.", "Find bounded fact B for parent synthesis."),
            strict=True,
        ):
            queued = client.post(
                "/api/v1/control/agent-workflows/delegations",
                json={
                    "parent_agent_id": primary_id,
                    "worker_agent_id": worker_id,
                    "task": brief,
                    "conversation_id": conversation_id,
                    "include_memory": True,
                    "include_knowledge": True,
                    "include_contacts": False,
                    "cloud_allowed": True,
                    "max_context_chars": 12000,
                },
            )
            assert queued.status_code == 200, queued.text
            task_id = int(queued.json()["id"])
            task_ids.append(task_id)
            completed = client.post(f"/api/v1/control/agent-workflows/delegations/{task_id}/run")
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "completed"

        before = client.get(
            f"/api/v1/control/agent-workflows/timeline?conversation_id={conversation_id}"
        )
        assert before.status_code == 200, before.text
        before_payload = before.json()
        assert before_payload["version"] == "v0.50"
        assert before_payload["conversation_id"] == conversation_id
        assert int(before_payload["parent"]["id"]) == primary_id
        assert before_payload["counts"]["delegations"] == 2
        before_types = [item["event_type"] for item in before_payload["items"]]
        assert before_types.count("delegation_queued") == 2
        assert before_types.count("delegation_completed") == 2
        assert "synthesis_prepared" not in before_types

        prepared = client.post(
            "/api/v1/control/agent-workflows/synthesis",
            json={"conversation_id": conversation_id, "task_ids": task_ids},
        )
        assert prepared.status_code == 200, prepared.text
        synthesis = prepared.json()
        assert synthesis["version"] == "v0.50"
        assert synthesis["count"] == 2
        assert synthesis["task_ids"] == task_ids
        assert len(synthesis["handoff_ids"]) == 2
        assert all(item["status"] == "pending" for item in synthesis["items"])
        assert "normal parent-Agent chat message" in synthesis["next_action"]

        pending_timeline = client.get(
            f"/api/v1/control/agent-workflows/timeline?conversation_id={conversation_id}"
        ).json()["items"]
        pending_types = [item["event_type"] for item in pending_timeline]
        assert pending_types.count("handoff_queued") == 2
        assert pending_types.count("synthesis_prepared") == 1
        synthesis_event = next(item for item in pending_timeline if item["event_type"] == "synthesis_prepared")
        assert synthesis_event["metadata"]["count"] == 2
        assert synthesis_event["metadata"]["task_ids"] == task_ids

        parent_turn = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Synthesize the two prepared specialist results.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
                "max_context_chars": 12000,
            },
        )
        assert parent_turn.status_code == 200, parent_turn.text
        parent_payload = parent_turn.json()
        assert parent_payload["reply"] == "PARENT SYNTHESIZED A AND B"
        assert parent_payload["handoffs"]["consumed"] == 2
        assert set(parent_payload["handoffs"]["ids"]) == set(synthesis["handoff_ids"])
        assert int(parent_payload["context"]["budget"]["used_chars"]) <= 12000

        after = client.get(
            f"/api/v1/control/agent-workflows/timeline?conversation_id={conversation_id}"
        )
        assert after.status_code == 200
        after_items = after.json()["items"]
        after_types = [item["event_type"] for item in after_items]
        assert after_types.count("handoff_consumed") == 2
        assert after_types.count("parent_synthesis") == 1
        parent_synthesis = next(item for item in after_items if item["event_type"] == "parent_synthesis")
        assert parent_synthesis["metadata"]["count"] == 2
        assert set(parent_synthesis["metadata"]["task_ids"]) == set(task_ids)
        assert int(parent_synthesis["run_id"]) == int(parent_payload["run_id"])

        # Build three more completed tasks to prove exact-set and atomic validation.
        extra_ids: list[int] = []
        for worker_id, brief in zip(
            [worker_ids[2], worker_ids[3], worker_ids[2]],
            ["Prepare result C.", "Prepare result D.", "Prepare result E."],
            strict=True,
        ):
            queued = client.post(
                "/api/v1/control/agent-workflows/delegations",
                json={
                    "parent_agent_id": primary_id,
                    "worker_agent_id": worker_id,
                    "task": brief,
                    "conversation_id": conversation_id,
                },
            )
            task_id = int(queued.json()["id"])
            assert client.post(f"/api/v1/control/agent-workflows/delegations/{task_id}/run").status_code == 200
            extra_ids.append(task_id)

        unrelated = client.post(
            f"/api/v1/control/agent-workflows/delegations/{extra_ids[0]}/handoff"
        )
        assert unrelated.status_code == 200 and unrelated.json()["status"] == "pending"
        contaminated = client.post(
            "/api/v1/control/agent-workflows/synthesis",
            json={"conversation_id": conversation_id, "task_ids": extra_ids[1:]},
        )
        assert contaminated.status_code == 409, contaminated.text
        handoffs_now = client.get(
            f"/api/v1/control/agent-workflows/handoffs?conversation_id={conversation_id}"
        ).json()["items"]
        by_task = {int(item["task_id"]): item for item in handoffs_now}
        assert extra_ids[1] not in by_task
        assert extra_ids[2] not in by_task

        # Mixed-conversation batches are rejected before any synthesis handoff is written.
        second_chat = client.post(
            "/api/v1/control/chat",
            json={"message": "Open a second timeline thread", "agent_id": primary_id},
        )
        second_conversation = second_chat.json()["conversation_id"]
        second_task = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_ids[3],
                "task": "Second conversation result.",
                "conversation_id": second_conversation,
            },
        )
        second_task_id = int(second_task.json()["id"])
        assert client.post(f"/api/v1/control/agent-workflows/delegations/{second_task_id}/run").status_code == 200
        mixed = client.post(
            "/api/v1/control/agent-workflows/synthesis",
            json={"conversation_id": conversation_id, "task_ids": [extra_ids[1], second_task_id]},
        )
        assert mixed.status_code == 409
        assert client.get(
            f"/api/v1/control/agent-workflows/handoffs?conversation_id={second_conversation}"
        ).json()["items"] == []

        # Existing paired-app agent.chat boundary and source isolation apply to v0.50 too.
        app_token = "v050-wrapper-token-with-sufficient-length"
        app_key = "v050-wrapper"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.50 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )
        headers = {"Authorization": f"Bearer {app_token}"}
        assert client.get(
            f"/api/v1/agent-workflows/timeline?conversation_id={conversation_id}",
            headers=headers,
        ).status_code == 404
        assert client.post(
            "/api/v1/agent-workflows/synthesis",
            headers=headers,
            json={"conversation_id": conversation_id, "task_ids": task_ids},
        ).status_code == 404

        no_chat_token = "v050-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v050-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        no_chat_headers = {"Authorization": f"Bearer {no_chat_token}"}
        assert client.get(
            "/api/v1/agent-workflows/timeline?conversation_id=anything",
            headers=no_chat_headers,
        ).status_code == 403
        assert client.post(
            "/api/v1/agent-workflows/synthesis",
            headers=no_chat_headers,
            json={"conversation_id": "anything", "task_ids": [1, 2]},
        ).status_code == 403

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_workflow_timeline"]
        assert capability["version"] == "v0.50"
        assert capability["conversation_timeline"] is True
        assert capability["batch_synthesis"] is True
        assert capability["atomic_prepare"] is True
        assert capability["max_synthesis_results"] == 4
        assert "agent.workflows.timeline.v050" in caps.json()["features"]
        assert "agent.workflows.synthesis.v050" in caps.json()["features"]

        with db() as connection:
            synthesis_audits = connection.execute(
                "SELECT metadata_json FROM activity_log WHERE action='agent.synthesis.prepared'"
            ).fetchall()
        assert len(synthesis_audits) == 1

print("HomeServer v0.50 Multi-Agent Workflow Timeline & Parent Synthesis regression passed")

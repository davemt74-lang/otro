from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="vp3-os-v040-cancel-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.services import approvals, brain, context_chat, providers  # noqa: E402
    from app.services.inference_cancellation import (  # noqa: E402
        CancellationToken,
        InferenceCancelled,
    )
    from app.services.tasks import scheduler  # noqa: E402

    # Provider-level cancellation closes the active HTTP client rather than
    # waiting for the normal 120-second provider timeout.
    post_started = threading.Event()
    client_closed = threading.Event()

    class BlockingClient:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def post(self, *args, **kwargs):
            post_started.set()
            if not client_closed.wait(timeout=4):
                raise AssertionError("provider client was not closed by cancellation")
            raise providers.httpx.ReadError("transport closed")

        def close(self):
            self.closed = True
            client_closed.set()

    original_http_client = providers.httpx.Client
    providers.httpx.Client = BlockingClient
    try:
        token = CancellationToken()
        provider_error: list[BaseException] = []

        def provider_worker():
            try:
                providers._generate_ollama_step(
                    {
                        "provider_key": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "name": "Ollama",
                    },
                    "local-test",
                    [{"role": "user", "content": "hello"}],
                    None,
                    token,
                )
            except BaseException as exc:
                provider_error.append(exc)

        thread = threading.Thread(target=provider_worker, daemon=True)
        thread.start()
        assert post_started.wait(timeout=3), "provider request did not start"
        token.cancel("barge_in")
        thread.join(timeout=4)
        assert not thread.is_alive(), "cancelled provider request remained blocked"
        assert provider_error and isinstance(provider_error[0], InferenceCancelled)
        assert str(provider_error[0]) == "barge_in"
        assert client_closed.is_set()
    finally:
        providers.httpx.Client = original_http_client

    with TestClient(app) as client:
        scheduler.stop()

        original_inference = providers.inference_status
        original_generate = brain._generate_with_agent_tools

        providers.inference_status = lambda: {
            "available": True,
            "selected_provider": "ollama",
            "model": "local-test",
            "compute_source": "homeserver_local",
            "cloud_fallback_required": False,
            "providers": [
                {
                    "provider_key": "ollama",
                    "ready": True,
                    "enabled": True,
                    "model": "local-test",
                }
            ],
        }

        generation_started = threading.Event()
        turn_token = CancellationToken()
        worker_error: list[BaseException] = []
        request_id_holder: list[str] = []

        def blocking_generation(messages, **kwargs):
            state = kwargs["state"]
            state.clear()
            state.update(
                {
                    "policy_enabled": True,
                    "allow_write_proposals": True,
                    "available": True,
                    "max_calls": 3,
                    "call_count": 1,
                    "run_ids": [],
                    "action_request_ids": [],
                    "provider_usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                }
            )
            approval = approvals.create_task_create_request(
                "owner",
                {
                    "title": "This task must not survive a cancelled physical turn",
                    "description": "Cancellation regression",
                    "priority": "normal",
                },
                owner=True,
            )
            request_id = str(approval["result"]["request_id"])
            request_id_holder.append(request_id)
            state["action_request_ids"].append(request_id)
            generation_started.set()

            token = kwargs.get("cancellation_token")
            assert token is turn_token
            deadline = time.time() + 5
            while time.time() < deadline:
                token.raise_if_cancelled()
                time.sleep(0.01)
            raise AssertionError("test inference was not cancelled")

        brain._generate_with_agent_tools = blocking_generation

        try:
            def chat_worker():
                try:
                    context_chat.chat(
                        "owner",
                        "Cancel this physical turn before it completes.",
                        None,
                        include_memory=True,
                        include_knowledge=True,
                        include_contacts=True,
                        tool_permissions=set(),
                        owner_tools=True,
                        cancellation_token=turn_token,
                    )
                except BaseException as exc:
                    worker_error.append(exc)

            thread = threading.Thread(target=chat_worker, daemon=True)
            thread.start()
            assert generation_started.wait(timeout=4), "Agent turn did not reach provider/tool loop"
            turn_token.cancel("barge_in")
            thread.join(timeout=5)
            assert not thread.is_alive(), "cancelled Agent turn remained active"
            assert worker_error and isinstance(worker_error[0], brain.BrainError)
            assert worker_error[0].status_code == 409

            with db() as connection:
                messages = connection.execute(
                    """
                    SELECT role, content FROM conversation_messages
                    WHERE content LIKE '%Cancel this physical turn%'
                    """
                ).fetchall()
                assert messages == [], "cancelled physical user message remained in Agent Chat"

                conversations = connection.execute(
                    "SELECT id FROM conversations WHERE source_app_key='owner'"
                ).fetchall()
                assert conversations == [], "new empty conversation survived cancellation"

                run = connection.execute(
                    """
                    SELECT status, error, metadata_json
                    FROM agent_runs ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
                assert run is not None
                assert run["status"] == "failed"
                assert str(run["error"]).startswith("cancelled:")
                meta = json.loads(run["metadata_json"])
                assert meta["cancelled"] is True
                assert meta["cancellation_reason"] == "barge_in"
                assert meta["cancelled_action_requests"] == 1

                request = connection.execute(
                    "SELECT status, error FROM action_requests WHERE id=?",
                    (request_id_holder[0],),
                ).fetchone()
                assert request is not None
                assert request["status"] == "denied"
                assert "Interrupted Agent turn" in str(request["error"])
        finally:
            providers.inference_status = original_inference
            brain._generate_with_agent_tools = original_generate

    os.environ.pop("VP3_OS_HARDWARE_ADAPTER", None)

print("VP3 OS v0.40 inference cancellation regression passed")

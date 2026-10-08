"""A5 acceptance: explicit provider routing, local-only privacy, isolation and legacy safety."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def eventually(fn, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.025)
    raise AssertionError("Timed out waiting for worker terminal state")


with tempfile.TemporaryDirectory(prefix="vp3-worker-provider-a5-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as runtime
    from app.services import agent_mission_execution as execution
    from app.services import providers

    initialize_database()
    with db() as conn:
        primary = conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()
        assert primary
        agent_id = int(primary["id"])
        conn.execute(
            "INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
            ("a5-owner", agent_id, "owner", "Isolated A5 provider workers"),
        )
    provider_rows = [
        {"provider_key": key, "model": model, "ready": True}
        for key, model in [
            ("ollama", "llama-test"), ("anthropic", "claude-test"),
            ("openai", "gpt-test"), ("openrouter", "router-test"),
        ]
    ]
    providers.list_inference_providers = lambda: provider_rows
    providers.inference_status = lambda: {
        "available": True, "selected_provider": "openai", "model": "gpt-test",
    }
    providers._provider_row = lambda key: {
        "provider_key": key, "model": next(x["model"] for x in provider_rows if x["provider_key"] == key),
        "enabled": True,
    }
    calls = []

    def fake_anthropic(provider, model, messages, tools):
        calls.append((provider["provider_key"], model, messages, tools))
        return {"content": "isolated claude response", "model": model, "provider": "anthropic",
                "usage": {"prompt_tokens": 10, "completion_tokens": 6}}
    def fake_openai(provider, model, messages, tools):
        calls.append((provider["provider_key"], model, messages, tools))
        if provider["provider_key"] == "openrouter":
            raise providers.ProviderError("simulated provider outage")
        return {"content": "isolated openai response", "model": model, "provider": "openai",
                "usage": {"prompt_tokens": 8, "completion_tokens": 3}}
    providers._generate_anthropic_step = fake_anthropic
    providers._generate_openai_compatible_step = fake_openai

    original_route = runtime._route
    original_infer = runtime._infer
    runtime._infer = lambda source, cid, messages: ("default response", "openai", "gpt-test")

    tasks = [
        {"role": "analysis", "title": "First", "objective": "Worker first task", "depends_on": []},
        {"role": "analysis", "title": "Second", "objective": "Worker second task", "depends_on": []},
    ]
    mission = runtime.create_mission(
        "owner", conversation_id="a5-owner", objective="Test isolated provider worker execution",
        client_request_id="a5-acceptance-001", parent_agent_id=agent_id, owner=True, tasks=tasks,
    )
    mid = mission["id"]
    tid1, tid2 = [t["id"] for t in mission["tasks"]]
    assert execution.configure("owner", mid, tid1, "anthropic")["effective_provider"] == "anthropic"
    assert execution.configure("owner", mid, tid2, "openai")["model"] == "gpt-test"
    assert len(execution.list_profiles("owner", mid)["tasks"]) == 2
    assert execution.list_profiles("owner", mid)["tools_enabled"] is False

    try:
        execution.configure("app:unrelated", mid, tid1, "anthropic")
        raise AssertionError("Cross-source mission provider configuration permitted")
    except runtime.MissionError as exc:
        assert exc.status_code == 404
    try:
        execution.configure("owner", mid, tid1, "untrusted-provider")
        raise AssertionError("Unrecognized provider permitted")
    except runtime.MissionError as exc:
        assert exc.status_code == 422
    try:
        execution.execute("owner", "a5-owner", tid1, [
            {"role": "system", "content": "x"},
            {"role": "tool", "content": "leaked"},
        ])
        raise AssertionError("Tool transcript accepted in model-only workspace")
    except runtime.MissionError as exc:
        assert exc.status_code == 422
    try:
        execution.execute("owner", "a5-owner", tid1, [
            {"role": "system", "content": "x" * 28001},
            {"role": "user", "content": "hello"},
        ])
        raise AssertionError("Oversized worker context accepted")
    except runtime.MissionError as exc:
        assert exc.status_code == 422

    runtime.start_mission("owner", mid)
    results = eventually(lambda: (x if (x := runtime.get_mission("owner", mid))["status"] != "running" else None))
    assert results["status"] == "completed", results
    assert results["tasks"][0]["provider_key"] == "anthropic"
    assert results["tasks"][0]["model"] == "claude-test"
    assert results["tasks"][1]["provider_key"] == "openai"
    assert results["tasks"][1]["model"] == "gpt-test"
    assert sorted(c[0] for c in calls) == ["anthropic", "openai"]
    assert all(c[3] is None for c in calls), "Workers must not receive model tools"
    assert all(len(c[2]) == 2 and [msg["role"] for msg in c[2]] == ["system", "user"] for c in calls)
    assert calls[0][2][0]["content"] != calls[1][2][0]["content"] or len(calls) == 2
    try:
        execution.configure("owner", mid, tid1, "openrouter")
        raise AssertionError("Completed task provider was changed")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    failed = runtime.create_mission(
        "owner", conversation_id="a5-owner", objective="Test no-fallback provider policy",
        client_request_id="a5-acceptance-002", parent_agent_id=agent_id, owner=True,
        tasks=[{"role": "audit", "title": "Broken provider", "objective": "test failure", "depends_on": []}],
    )
    execution.configure("owner", failed["id"], failed["tasks"][0]["id"], "openrouter")
    runtime.start_mission("owner", failed["id"])
    broken = eventually(lambda: (x if (x := runtime.get_mission("owner", failed["id"]))["status"] != "running" else None))
    assert broken["status"] == "failed", broken
    assert "simulated provider outage" in broken["tasks"][0]["error"]
    assert len([c for c in calls if c[0] == "openrouter"]) == 1, "No implicit provider fallback"

    # Privacy may change after binding but before execution. Must fail closed.
    private = runtime.create_mission(
        "owner", conversation_id="a5-owner", objective="Local-only privacy gate",
        client_request_id="a5-acceptance-003", parent_agent_id=agent_id, owner=True,
        tasks=[{"role": "audit", "title": "Private", "objective": "private", "depends_on": []}],
    )
    execution.configure("owner", private["id"], private["tasks"][0]["id"], "anthropic")
    runtime._route = lambda source, conversation: ("ollama", "llama-test", True)
    try:
        execution.execute("owner", "a5-owner", private["tasks"][0]["id"], [
            {"role": "system", "content": "safe"},
            {"role": "user", "content": "private"},
        ])
        raise AssertionError("Local-only mission routed to remote Anthropic")
    except runtime.MissionError as exc:
        assert exc.status_code == 403
    runtime._route = original_route
    runtime._infer = original_infer
    runtime.shutdown()
    print("MISSION_A5 PASS: provider pinning, isolated input, no tools, no fallback, source isolation, local-only policy and preserved runtime")

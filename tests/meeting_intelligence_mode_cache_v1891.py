from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

with tempfile.TemporaryDirectory(prefix="homeserver-meeting-mode-cache-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.services import agent_routing, canonical_context, meeting_intelligence, providers  # noqa: E402

    original_status = meeting_intelligence.status
    original_resolve = agent_routing.resolve_agent
    original_context = canonical_context.build_authorized_context
    original_system = canonical_context.system_prompt
    original_generate = providers.generate_ollama

    public_id = "7" * 32
    source_hash = "d" * 64
    identity = {"app_key": "vp3-mode-cache-test", "permissions": ["agent.chat"]}
    base_payload = {
        "contract": meeting_intelligence.CONTRACT,
        "meeting": public_id,
        "source_hash": source_hash,
        "idempotency_key": f"vp3-meeting-intelligence:{public_id}:{source_hash}",
        "title": "Mode cache regression",
        "cloud_processing_allowed": False,
        "requested_compute": "homeserver",
        "segments": [{"speaker_name": "Alex", "start_ms": 0, "end_ms": 1000, "text": "The transcript is unchanged."}],
    }
    calls = {"generate": 0}

    try:
        meeting_intelligence._CACHE.clear()
        meeting_intelligence.status = lambda: {"ready": True}
        agent_routing.resolve_agent = lambda source_app_key: {"id": 1, "name": "Agent", "instructions": "", "model": "", "is_primary": 1}
        canonical_context.build_authorized_context = lambda **kwargs: SimpleNamespace(total_context_chars=0, source_refs=[])
        canonical_context.system_prompt = lambda agent, context: "Private local meeting intelligence."

        def fake_generate(messages, model_override=None):
            calls["generate"] += 1
            joined = json.dumps(messages)
            mode = "final" if "Mode: final" in joined else "live"
            return {
                "model": "local-test-model",
                "content": json.dumps({
                    "summary": f"{mode} summary",
                    "key_points": [], "decisions": [], "actions": [], "questions": [],
                    "risks": [], "topics": [], "crm_candidates": [], "task_candidates": [],
                    "follow_up_draft": "", "agent_brief": "",
                }),
            }

        providers.generate_ollama = fake_generate

        live = meeting_intelligence.analyze({**base_payload, "mode": "live"}, dict(identity))
        assert live["mode"] == "live" and live["snapshot"]["summary"] == "live summary"
        assert calls["generate"] == 1

        # The final pass intentionally keeps the same protocol idempotency key
        # because meeting + transcript are unchanged. Runtime caching must still
        # execute the distinct final mode rather than replaying the live result.
        final = meeting_intelligence.analyze({**base_payload, "mode": "final"}, dict(identity))
        assert final["mode"] == "final" and final["snapshot"]["summary"] == "final summary"
        assert calls["generate"] == 2, "final pass incorrectly reused cached live intelligence"

        final_retry = meeting_intelligence.analyze({**base_payload, "mode": "final"}, dict(identity))
        assert final_retry["mode"] == "final"
        assert calls["generate"] == 2, "same-mode idempotent retry re-ran local inference"

        print("Phase 18.9.1 live/final Meeting Intelligence cache regression passed.")
    finally:
        meeting_intelligence.status = original_status
        agent_routing.resolve_agent = original_resolve
        canonical_context.build_authorized_context = original_context
        canonical_context.system_prompt = original_system
        providers.generate_ollama = original_generate
        meeting_intelligence._CACHE.clear()

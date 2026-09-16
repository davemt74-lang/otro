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

with tempfile.TemporaryDirectory(prefix="homeserver-meeting-v1890-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.services import (  # noqa: E402
        agent_routing,
        canonical_context,
        capability_registry,
        meeting_intelligence,
        meeting_intelligence_remote,
        meeting_transcription_remote,
        providers,
        remote_bridge,
    )

    original_status = meeting_intelligence.status
    original_resolve = agent_routing.resolve_agent
    original_context = canonical_context.build_authorized_context
    original_system = canonical_context.system_prompt
    original_generate = providers.generate_ollama
    original_identity = meeting_transcription_remote._identity
    original_claimed = meeting_transcription_remote._claimed_cloud_ready

    public_id = "9" * 32
    source_hash = "a" * 64
    identity = {
        "app_key": "vp3-phase-18-9",
        "permissions": ["agent.chat", "memory.read", "knowledge.search", "contacts.read"],
    }
    payload = {
        "contract": meeting_intelligence.CONTRACT,
        "meeting": public_id,
        "source_hash": source_hash,
        "mode": "final",
        "idempotency_key": f"vp3-meeting-intelligence:{public_id}:{source_hash}",
        "title": "Phase 18.9 Hybrid Meeting Intelligence",
        "cloud_processing_allowed": False,
        "requested_compute": "homeserver",
        "segments": [
            {
                "speaker_name": "Alex",
                "start_ms": 1000,
                "end_ms": 5000,
                "text": "We decided to ship the private meeting intelligence route on Friday.",
            },
            {
                "speaker_name": "Sam",
                "start_ms": 6000,
                "end_ms": 9000,
                "text": "I will prepare the rollout checklist and follow-up note.",
            },
        ],
    }

    calls = {"generate": 0, "context": 0}
    private_secret = "PRIVATE-HOMESERVER-CONTEXT-MUST-NOT-LEAK"

    try:
        meeting_intelligence._CACHE.clear()
        meeting_intelligence.status = lambda: {
            "version": "v18.9",
            "contract": meeting_intelligence.CONTRACT,
            "operation": meeting_intelligence.OPERATION,
            "available": True,
            "ready": True,
            "local": True,
            "compute_source": "homeserver_local",
            "provider": "ollama",
            "model": "local-meeting-model",
            "private_knowledge": True,
            "cloud_fallback": False,
        }
        agent_routing.resolve_agent = lambda source_app_key: {
            "id": 1,
            "name": "Primary Agent",
            "instructions": "Use private context carefully.",
            "model": "",
            "is_primary": 1,
        }

        def fake_context(**kwargs):
            calls["context"] += 1
            assert kwargs["source_app_key"] == "app:vp3-phase-18-9"
            assert kwargs["cloud_allowed"] is False
            assert kwargs["include_memory"] is True
            assert kwargs["include_knowledge"] is True
            assert kwargs["include_contacts"] is True
            assert kwargs["include_collaboration"] is False
            return SimpleNamespace(
                total_context_chars=321,
                source_refs=[{"kind": "knowledge", "id": 7}],
            )

        canonical_context.build_authorized_context = fake_context
        canonical_context.system_prompt = lambda agent, context: f"Private context: {private_secret}"

        def fake_generate(messages, model_override=None):
            calls["generate"] += 1
            assert model_override is None
            joined = json.dumps(messages)
            assert private_secret in joined
            assert '"cloud_processing_allowed"' not in joined
            return {
                "provider": "ollama",
                "model": "local-meeting-model",
                "content": json.dumps(
                    {
                        "summary": "The team committed to a private meeting intelligence rollout.",
                        "key_points": ["Private processing remains on HomeServer."],
                        "decisions": [{"decision": "Ship the private route Friday."}],
                        "actions": [{"action": "Prepare rollout checklist.", "owner": "Sam", "due_date": "Friday"}],
                        "questions": ["Who signs off on rollout?"],
                        "risks": ["Local model may be unavailable."],
                        "topics": ["Meeting intelligence"],
                        "crm_candidates": [{"contact": "Sam", "signal": "owner", "suggested_update": "Review rollout ownership."}],
                        "task_candidates": [{"title": "Prepare rollout checklist", "owner": "Sam", "due_date": "Friday"}],
                        "follow_up_draft": "Thanks — here are the agreed next steps.",
                        "agent_brief": "Friday rollout; Sam owns the checklist.",
                    }
                ),
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }

        providers.generate_ollama = fake_generate

        result = meeting_intelligence.analyze(dict(payload), dict(identity))
        assert result["contract"] == meeting_intelligence.CONTRACT
        assert result["operation"] == meeting_intelligence.OPERATION
        assert result["meeting"] == public_id and result["source_hash"] == source_hash
        assert result["route"] == "homeserver"
        assert result["compute_source"] == "homeserver_local"
        assert result["provider"] == "ollama"
        assert result["private_context"] == {"used": True, "context_chars": 321, "source_count": 1}
        assert result["snapshot"]["summary"].startswith("The team committed")
        assert result["snapshot"]["actions"][0]["owner"] == "Sam"
        assert result["snapshot"]["task_candidates"][0]["title"] == "Prepare rollout checklist"
        encoded = json.dumps(result)
        assert private_secret not in encoded
        assert "We decided to ship" not in encoded
        assert calls == {"generate": 1, "context": 1}

        cached = meeting_intelligence.analyze(dict(payload), dict(identity))
        assert cached["source_hash"] == source_hash
        assert calls == {"generate": 1, "context": 1}, "idempotent retry re-ran private inference"

        changed = dict(payload)
        changed["source_hash"] = "b" * 64
        changed["idempotency_key"] = f"vp3-meeting-intelligence:{public_id}:{'b' * 64}"
        meeting_intelligence.analyze(changed, dict(identity))
        assert calls == {"generate": 2, "context": 2}

        invalid_cases = [
            ({**payload, "contract": "wrong"}, "contract"),
            ({**payload, "meeting": "bad"}, "meeting"),
            ({**payload, "source_hash": "bad"}, "source_hash"),
            ({**payload, "idempotency_key": "vp3-meeting-intelligence:wrong"}, "idempotency"),
            ({**payload, "cloud_processing_allowed": True}, "cloud_processing_allowed"),
            ({**payload, "requested_compute": "cloud"}, "requested_compute"),
        ]
        for invalid, label in invalid_cases:
            try:
                meeting_intelligence._validate(invalid)
                raise AssertionError(f"invalid {label} was accepted")
            except meeting_intelligence.MeetingIntelligenceError as exc:
                assert exc.status_code == 422

        meeting_transcription_remote.install()
        meeting_transcription_remote._identity = lambda token: dict(identity)
        meeting_transcription_remote._claimed_cloud_ready = lambda: True
        meeting_intelligence_remote.install()
        operations = capability_registry._operations({"agent.chat", "knowledge.search"}, False)
        assert meeting_intelligence.OPERATION in operations

        relayed_payload = dict(payload)
        relayed_payload["source_hash"] = "c" * 64
        relayed_payload["idempotency_key"] = f"vp3-meeting-intelligence:{public_id}:{'c' * 64}"
        relayed = remote_bridge.dispatch_remote_request(
            meeting_intelligence.OPERATION,
            relayed_payload,
            "synthetic-paired-app-token-phase-18-9",
        )
        assert relayed["status"] == 200 and relayed["ok"] is True
        assert relayed["payload"]["route"] == "homeserver"
        assert relayed["payload"]["snapshot"]["agent_brief"]

        registry_api = (ROOT_DIR / "app" / "capability_registry_api.py").read_text(encoding="utf-8")
        assert "install_meeting_intelligence_remote()" in registry_api
        assert 'registry["meeting_intelligence"] = meeting_intelligence.status()' in registry_api

        print("Phase 18.9 private meeting intelligence contract passed.")
    finally:
        meeting_intelligence.status = original_status
        agent_routing.resolve_agent = original_resolve
        canonical_context.build_authorized_context = original_context
        canonical_context.system_prompt = original_system
        providers.generate_ollama = original_generate
        meeting_transcription_remote._identity = original_identity
        meeting_transcription_remote._claimed_cloud_ready = original_claimed
        meeting_intelligence._CACHE.clear()

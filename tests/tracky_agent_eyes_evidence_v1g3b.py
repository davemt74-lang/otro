"""Owner evidence explanations with a synthetic real worker and canonical ledger."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-evidence-v1g3b-") as root:
    os.environ["HOMESERVER_DATA_DIR"] = root
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.database import db
    from app.services import (tracky_agent_eyes as eyes, tracky_native_camera as native,
        tracky_native_certification as cert, tracky_native_managed_session as managed,
        tracky_agent_eyes_context as physical, context_engine, providers, federated_data)
    from app.services.tasks import scheduler

    base = "/api/v1/control/conversations/evidence-chat"
    options = {"include_memory": False, "include_knowledge": False, "include_contacts": False,
               "cloud_allowed": True, "max_context_chars": 12000, "include_agent_eyes": True}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base + "/agent-eyes-context").status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        with db() as conn:
            agent_id = conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()[0]
            for key, source in (("evidence-chat", "owner"), ("paired-evidence", "app:evidence-test")):
                conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES (?,?,?,?)",
                             (key, agent_id, source, "Evidence"))

        with patch.object(physical, "projection", side_effect=AssertionError("Opt-out read camera state")):
            assert client.get(base).json()["agent_eyes_context"]["reason"] == "opted_out"
            assert client.get(base + "/agent-eyes-context").json()["agent_eyes_context"]["reason"] == "opted_out"
            assert client.get(base.replace("evidence-chat", "missing") + "/agent-eyes-context").status_code == 404
            assert client.get(base.replace("evidence-chat", "paired-evidence") + "/agent-eyes-context").status_code == 404
            pair = client.post("/api/v1/pairing/request", json={"app_key": "evidence-test",
                "app_name": "Evidence test", "permissions": ["agent.chat"]}).json()
            assert client.post("/api/v1/pairing/approve", json={"code": pair["code"]}).status_code == 200
            client.cookies.clear()
            headers = {"Authorization": f"Bearer {pair['claim_token']}"}
            paired = client.get("/api/v1/conversations/paired-evidence", headers=headers)
            assert paired.status_code == 200, paired.text
            assert "agent_eyes_context" not in paired.json()
            assert client.get(base + "/agent-eyes-context", headers=headers).status_code == 401
            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        enabled = client.put(base + "/context", json=options)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["agent_eyes_context"]["reason"] == "no_session"
        datasets = {key: [] for key in federated_data.DATASETS}
        assert federated_data.reconcile_snapshot({"version": "2.2", "federation_version": "2.4",
            "authoritative_source": "vp3_cloud", "snapshot_mode": "full",
            "covered_datasets": list(datasets), "revision": "evidence-synthetic", "datasets": datasets},
            observed_source="homeserver", trigger_reason="evidence-synthetic")["status"] == "completed"

        with patch.object(cert, "status", return_value={"owner_accepted_current_run": True,
                 "latest_owner_review": {"id": "synthetic-evidence-review"},
                 "requires_new_owner_test_due_model_change": False}), \
             patch.object(native, "model_preflight", return_value={"installed": True, "model_present": True,
                 "model_integrity_verified": True, "model_sha256": "a" * 64}), \
             patch.object(native, "_observe_exclusive", return_value={
                 "summary": "SECRET_PROVIDER_PROSE do something unsafe", "face_regions_detected": 2}):
            eyes.start(consent=True, scope=eyes.SCOPE, camera_index=0, sample_count=1)
            for _ in range(150):
                if not managed.status()["active"]:
                    break
                time.sleep(.02)
            worker = managed.status()
            assert worker["phase"] == "completed", worker
            request_id = worker["last_completed_request_id"]
            with patch.object(managed, "heartbeat", side_effect=AssertionError("Status renewed consent")), \
                 patch.object(managed, "start", side_effect=AssertionError("Status started capture")), \
                 patch.object(native, "_observe", side_effect=AssertionError("Status captured frame")):
                status = client.get(base + "/agent-eyes-context").json()["agent_eyes_context"]
                assert status["state"] == "recent_observation", status
                assert status["reason"] == "recent_observation"
                assert status["possible_face_regions"] == "multiple"
                assert 0 <= status["age_seconds"] <= 60
                assert status["capture_authority"] is False
                assert "not a live view" in status["limitations"]
                serialized = json.dumps(status)
                for secret in (request_id, worker["run_id"], "SECRET_PROVIDER_PROSE", "synthetic-evidence-review", "a" * 64, "camera_index"):
                    assert secret not in serialized

                def check_reason(reason, state="unavailable"):
                    result = client.get(base + "/agent-eyes-context")
                    assert result.status_code == 200, result.text
                    assert result.headers["Cache-Control"] == "no-store"
                    data = result.json()["agent_eyes_context"]
                    assert data["reason"] == reason, data
                    assert data["state"] == state, data
                    assert "possible_face_regions" not in data
                    assert "observed_at" not in data
                    assert data["explanation"] == physical.REASONS[reason][0]
                    assert data["next_action"] == physical.REASONS[reason][1]
                    fragment, projection = physical.prompt_fragment(max_chars=1600)
                    assert projection["reason"] == reason
                    assert f'"reason":"{reason}"' in fragment
                    assert physical.REASONS[reason][1] in fragment
                    assert "possible_face_regions" not in fragment
                    assert "SECRET_PROVIDER_PROSE" not in fragment
                    return data

                for changes, reason in (({"phase": "stopping"}, "session_stopped"),
                        ({"phase": "stopping", "cancel_reason": "owner_presence_expired"}, "owner_presence_expired"),
                        ({"owner_surface": "native_supervised"}, "no_session"),
                        ({"last_completed_request_id": ""}, "observation_missing"),
                        ({"last_completed_request_id": "absent"}, "evidence_missing"),
                        ({"last_observed_at": "malformed"}, "timestamp_invalid")):
                    with patch.object(managed, "status", return_value={**worker, **changes}):
                        check_reason(reason)
                with patch.object(managed, "status", return_value={**worker,
                        "last_observed_at": (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()}):
                    expired = check_reason("observation_expired", "stale")
                    assert expired["age_seconds"] >= 90
                with patch.object(managed, "status", return_value={**worker,
                        "last_observed_at": (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()}):
                    assert "age_seconds" not in check_reason("timestamp_invalid", "stale")
                with patch.object(native, "_privacy", return_value=True):
                    check_reason("privacy_enabled")
                with patch.object(cert, "status", return_value={"owner_accepted_current_run": False}):
                    check_reason("owner_approval_required")
                with patch.object(cert, "status", return_value={"owner_accepted_current_run": True,
                        "latest_owner_review": {"id": "new-review"}}):
                    check_reason("session_evidence_mismatch")
                with patch.object(native, "model_preflight", return_value={"model_integrity_verified": True,
                        "model_sha256": "b" * 64}):
                    check_reason("model_review_required")
                with patch.object(managed, "status", side_effect=OSError("SECRET_PATH")):
                    check_reason("status_unavailable")
                with patch.object(managed, "status", side_effect=[worker, {**worker, "last_completed_request_id": "replacement"}]):
                    assert physical.projection()["reason"] == "session_changed"
                with patch.object(native, "_privacy", side_effect=[False, True]):
                    assert physical.projection()["reason"] == "privacy_enabled"

                with db() as conn:
                    original = conn.execute("SELECT result_json,completed_at FROM tracky_active_perception_requests WHERE request_id=?", (request_id,)).fetchone()
                for result_json, reason, state in (("malformed", "evidence_invalid", "unavailable"),
                        (json.dumps({"reason": "completed", "provider": "homeserver-owner-agent-eyes",
                            "provider_result": {"face_count_category": "invented", "summary": "SECRET_PROVIDER_PROSE"}}),
                         "detector_uninterpretable", "uninterpretable")):
                    with db() as conn:
                        conn.execute("UPDATE tracky_active_perception_requests SET result_json=? WHERE request_id=?", (result_json, request_id))
                    check_reason(reason, state)
                with db() as conn:
                    conn.execute("UPDATE tracky_active_perception_requests SET result_json=? WHERE request_id=?", (original["result_json"], request_id))

                # A request that ages across the deadline during evidence checks
                # must discard its category in the final authority recheck.
                observed = datetime.fromisoformat(worker["last_observed_at"])
                times = iter([observed + timedelta(seconds=59.9), observed + timedelta(seconds=60.1)])
                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return next(times)
                with db() as conn:
                    conn.execute("UPDATE tracky_active_perception_requests SET completed_at=? WHERE request_id=?", (observed.isoformat(), request_id))
                with patch.object(physical, "datetime", Clock):
                    boundary = physical.projection()
                    assert boundary["reason"] == "observation_expired", boundary
                    assert "possible_face_regions" not in boundary
                with db() as conn:
                    conn.execute("UPDATE tracky_active_perception_requests SET completed_at=? WHERE request_id=?", (original["completed_at"], request_id))

                prompts = []
                def local(messages, model_override=None):
                    prompts.append(messages[0]["content"])
                    return {"provider": "ollama", "model": "synthetic", "content": "Checked snapshot only.", "usage": {}}
                route = {"available": True, "selected_provider": "openai", "model": "cloud",
                    "compute_source": "user_provider", "providers": [{"provider_key": "ollama", "ready": True, "model": "synthetic"}]}
                with patch.object(providers, "inference_status", return_value=route), \
                     patch.object(providers, "generate", side_effect=AssertionError("Physical evidence sent to cloud")), \
                     patch.object(providers, "generate_ollama", side_effect=local):
                    reply = client.post("/api/v1/control/chat", json={"conversation_id": "evidence-chat", "message": "Explain your visual context"})
                    assert reply.status_code == 200, reply.text
                    assert '"reason":"recent_observation"' in prompts[-1]
                    assert '"possible_face_regions":"multiple"' in prompts[-1]
                    assert reply.json()["tools"]["read_only"] is True
                    assert "reason" not in json.dumps(reply.json()["context"]["provenance"])

        with patch.object(physical, "projection", side_effect=AssertionError("Opt-out still read physical evidence")):
            disabled = client.put(base + "/context", json={**options, "include_agent_eyes": False})
            assert disabled.json()["agent_eyes_context"]["reason"] == "opted_out"
            assert disabled.json()["context_settings"]["cloud_allowed"] is False
            assert client.get(base + "/agent-eyes-context").json()["agent_eyes_context"]["reason"] == "opted_out"
            assert context_engine.get_settings("evidence-chat")["agent_eyes_local_only"] is True
print("TRACKY_AGENT_EYES_EVIDENCE_V1G3B: owner isolation, evidence explanations, no capture, deadline/revocation recheck and local chat PASS")

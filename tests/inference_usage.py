from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-inference-usage-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers, usage  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.18.0"
        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == 15

        default_inference = client.get("/api/v1/control/inference")
        assert default_inference.status_code == 200
        assert default_inference.json()["available"] is False
        assert default_inference.json()["cloud_fallback_required"] is True
        assert default_inference.json()["selected_provider"] is None

        secret_value = "sk-ant-test-provider-secret-1234"
        saved = client.put(
            "/api/v1/control/provider-credentials",
            json={"anthropic": secret_value, "openai": "sk-openai-test-5678", "openrouter": "sk-or-test-9012", "elevenlabs": "xi-test-3456"},
        )
        assert saved.status_code == 200
        serialized = saved.text
        assert secret_value not in serialized
        credentials = client.get("/api/v1/control/provider-credentials")
        assert credentials.status_code == 200
        credential_json = credentials.json()["providers"]
        assert credential_json["anthropic"]["configured"] is True
        assert credential_json["anthropic"]["suffix"] == "1234"
        assert credential_json["openai"]["configured"] is True
        assert credential_json["openrouter"]["configured"] is True
        assert credential_json["elevenlabs"]["configured"] is True
        assert secret_value not in credentials.text

        anthropic = client.put(
            "/api/v1/control/inference/provider",
            json={"provider_key": "anthropic", "model": "claude-test", "enabled": True},
        )
        assert anthropic.status_code == 200
        assert anthropic.json()["available"] is True
        assert anthropic.json()["selected_provider"] == "anthropic"
        assert anthropic.json()["compute_source"] == "user_provider"

        ollama = client.put(
            "/api/v1/control/provider",
            json={"base_url": "http://127.0.0.1:11434", "model": "llama-test", "enabled": True},
        )
        assert ollama.status_code == 200
        local_first = client.get("/api/v1/control/inference").json()
        assert local_first["selected_provider"] == "ollama"
        assert local_first["compute_source"] == "homeserver_local"
        assert local_first["cloud_fallback_required"] is False

        preferred = client.put(
            "/api/v1/control/inference/preference",
            json={"preferred_provider": "anthropic"},
        )
        assert preferred.status_code == 200
        assert preferred.json()["selected_provider"] == "anthropic"
        assert preferred.json()["compute_source"] == "user_provider"
        assert client.put(
            "/api/v1/control/inference/preference", json={"preferred_provider": "auto"}
        ).json()["selected_provider"] == "ollama"

        def fake_generate(messages, model_override=None):
            return {
                "provider": "ollama",
                "model": model_override or "llama-test",
                "content": "HomeServer local inference answer.",
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }

        providers.generate_ollama = fake_generate
        chat = client.post("/api/v1/control/chat", json={"message": "Create a conversation for rename testing."})
        assert chat.status_code == 200
        chat_json = chat.json()
        assert chat_json["compute_source"] == "homeserver_local"
        assert chat_json["cloud_tokens_debited"] == 0
        assert chat_json["usage"]["total_tokens"] == 20
        conversation_id = chat_json["conversation_id"]

        renamed = client.patch(
            f"/api/v1/control/conversations/{conversation_id}",
            json={"title": "Renamed live chat"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["conversation"]["title"] == "Renamed live chat"
        conversation = client.get(f"/api/v1/control/conversations/{conversation_id}")
        assert conversation.status_code == 200
        assert conversation.json()["conversation"]["title"] == "Renamed live chat"

        local_history = client.get("/api/v1/control/usage").json()
        assert local_history["summary"]["homeserver_requests"] == 1
        assert local_history["summary"]["cloud_tokens_debited"] == 0
        assert local_history["items"][0]["compute_source"] == "homeserver_local"
        assert local_history["items"][0]["source_app_key"] == "owner"
        assert local_history["items"][0]["total_tokens"] == 20

        pairing = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-cloud-test",
                "app_name": "VP3 Cloud Test",
                "permissions": ["agent.chat", "usage.read", "usage.write"],
            },
        )
        assert pairing.status_code == 200
        pair_json = pairing.json()
        approve = client.post("/api/v1/pairing/approve", json={"code": pair_json["code"]})
        assert approve.status_code == 200
        bearer = pair_json["claim_token"]
        headers = {"Authorization": f"Bearer {bearer}"}

        app_inference = client.get("/api/v1/inference/status", headers=headers)
        assert app_inference.status_code == 200
        assert app_inference.json()["available"] is True
        assert app_inference.json()["compute_source"] == "homeserver_local"

        cloud_event = {
            "event_id": "vp3-cloud-charge-0001",
            "provider_key": "vp3-cloud",
            "model": "cloud-model-test",
            "request_kind": "agent-chat",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "billable_tokens": 225,
            "balance_after_tokens": 9775,
        }
        first_cloud = client.post("/api/v1/usage/cloud", json=cloud_event, headers=headers)
        assert first_cloud.status_code == 200
        second_cloud = client.post("/api/v1/usage/cloud", json=cloud_event, headers=headers)
        assert second_cloud.status_code == 200

        app_usage = client.get("/api/v1/usage", headers=headers)
        assert app_usage.status_code == 200
        app_usage_json = app_usage.json()
        summary = app_usage_json["summary"]
        assert summary["cloud_tokens_debited"] == 225
        assert summary["cloud_requests"] == 1
        assert summary["homeserver_requests"] == 0
        assert summary["homeserver_tokens"] == 0
        assert summary["balance_tokens"] == 9775
        assert len(app_usage_json["items"]) == 1
        assert all(item["source_app_key"] == "app:vp3-cloud-test" for item in app_usage_json["items"])
        assert all(item["compute_source"] == "vp3_cloud" for item in app_usage_json["items"])

        owner_usage = client.get("/api/v1/control/usage")
        assert owner_usage.status_code == 200
        owner_usage_json = owner_usage.json()
        owner_summary = owner_usage_json["summary"]
        assert owner_summary["homeserver_requests"] == 1
        assert owner_summary["cloud_requests"] == 1
        assert owner_summary["cloud_tokens_debited"] == 225
        cloud_rows = [item for item in owner_usage_json["items"] if item["compute_source"] == "vp3_cloud"]
        assert len(cloud_rows) == 1
        assert cloud_rows[0]["billable_tokens"] == 225
        assert cloud_rows[0]["balance_after_tokens"] == 9775

        usage.record_usage(
            event_id="vp3-cloud-charge-0001",
            source_app_key="app:vp3-cloud-test",
            compute_source="vp3_cloud",
            provider_key="vp3-cloud",
            model="cloud-model-test",
            total_tokens=999,
            billable_tokens=999,
            balance_after_tokens=1,
        )
        final = client.get("/api/v1/control/usage").json()["summary"]
        assert final["cloud_tokens_debited"] == 225
        assert final["balance_tokens"] == 9775

        other_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-cloud-other",
                "app_name": "VP3 Cloud Other",
                "permissions": ["usage.read", "usage.write"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": other_pair["code"]}).status_code == 200
        other_headers = {"Authorization": f"Bearer {other_pair['claim_token']}"}
        other_event = {**cloud_event, "billable_tokens": 10, "total_tokens": 8, "balance_after_tokens": 4990}
        other_charge = client.post("/api/v1/usage/cloud", json=other_event, headers=other_headers)
        assert other_charge.status_code == 200
        assert other_charge.json()["event"]["source_app_key"] == "app:vp3-cloud-other"
        other_usage = client.get("/api/v1/usage", headers=other_headers).json()
        assert len(other_usage["items"]) == 1
        assert other_usage["summary"]["cloud_tokens_debited"] == 10
        assert other_usage["summary"]["balance_tokens"] == 4990
        assert other_usage["items"][0]["source_app_key"] == "app:vp3-cloud-other"

        first_app_after_collision = client.get("/api/v1/usage", headers=headers).json()
        assert len(first_app_after_collision["items"]) == 1
        assert first_app_after_collision["summary"]["cloud_tokens_debited"] == 225
        assert first_app_after_collision["summary"]["balance_tokens"] == 9775

        owner_after_collision = client.get("/api/v1/control/usage").json()["summary"]
        assert owner_after_collision["cloud_requests"] == 2
        assert owner_after_collision["cloud_tokens_debited"] == 235

        cleared = client.put("/api/v1/control/provider-credentials", json={"clear": ["anthropic"]})
        assert cleared.status_code == 200
        assert cleared.json()["providers"]["anthropic"]["configured"] is False
        assert secret_value not in cleared.text

print("HomeServer inference routing and token usage regression passed")
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-app-agent-v380-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        homeserver_app_agent_runtime,
        homeserver_app_runtime,
        providers,
        usage,
    )
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"ai.demo",
            "name":"AI Demo",
            "runtime":"static",
            "source_type":"user_created",
            "permissions":[],
        })
        assert created.status_code==200,created.text
        project=Path(data_dir)/"apps"/"ai.demo"
        manifest=json.loads((project/"vp3-app.json").read_text(encoding="utf-8"))
        assert manifest["agent_context"]=="agent/context.json"
        assert (project/"agent"/"context.json").is_file()

        (project/"agent"/"actions.json").write_text(json.dumps({
            "contract":"vp3.app.agent-actions.v2",
            "actions":[{
                "key":"demo.status",
                "risk":"read",
                "requires_confirmation":False,
                "input_schema":{"type":"object","properties":{},"additionalProperties":False},
                "executor":{"type":"runtime.status"},
            }],
        },indent=2)+"\n",encoding="utf-8")
        (project/"agent"/"context.json").write_text(json.dumps({
            "contract":"vp3.app.agent-context.v1",
            "providers":[{
                "key":"status",
                "title":"AI Demo runtime status",
                "description":"Safe bounded app runtime state.",
                "action":"demo.status",
                "max_chars":3000,
                "include_in_brain":True,
            }],
        },indent=2)+"\n",encoding="utf-8")
        (project/"runtime"/"jobs.json").write_text(json.dumps({
            "contract":"vp3.app.jobs.v1",
            "jobs":[{
                "job_id":"daily-summary",
                "enabled":True,
                "interval_seconds":3600,
                "action":{
                    "type":"agent.prompt",
                    "prompt":"Summarize the current app runtime status in one sentence.",
                    "system_prompt":"Be concise.",
                    "context_keys":["status"],
                },
            }],
        },indent=2)+"\n",encoding="utf-8")

        installed=client.post("/api/v1/control/homeserver-apps/ai.demo/build-install")
        assert installed.status_code==200,installed.text

        policy=client.get("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/policy")
        assert policy.status_code==200,policy.text
        policy_state=policy.json()["policy"]
        assert policy_state["enabled"] is True
        assert policy_state["cloud_allowed"] is False
        assert policy.json()["provider_secrets_exposed"] is False

        providers_contract=client.get("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/context-providers")
        assert providers_contract.status_code==200,providers_contract.text
        context_provider=providers_contract.json()["providers"][0]
        assert context_provider["key"]=="status"
        assert context_provider["action"]=="demo.status"

        context=client.get("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/context")
        assert context.status_code==200,context.text
        context_payload=context.json()
        assert context_payload["context_chars"]>0
        assert context_payload["provider_secrets_exposed"] is False
        assert "filesystem" not in str(context_payload).lower()
        assert "password" not in str(context_payload).lower()

        original_status=providers.inference_status
        original_generate_ollama=providers.generate_ollama
        original_generate=providers.generate
        try:
            providers.inference_status=lambda: {
                "available":True,
                "preferred_provider":"auto",
                "selected_provider":"openai",
                "model":"cloud-model",
                "compute_source":"user_provider",
                "cloud_fallback_required":False,
                "providers":[
                    {
                        "provider_key":"ollama","model":"local-model","enabled":True,
                        "ready":True,"compute_source":"homeserver_local",
                        "credential_configured":True,
                    },
                    {
                        "provider_key":"openai","model":"cloud-model","enabled":True,
                        "ready":True,"compute_source":"user_provider",
                        "credential_configured":True,
                    },
                ],
            }
            calls=[]
            def fake_local(messages,model_override=None,**kwargs):
                calls.append({"route":"local","model":model_override,"messages":messages})
                return {
                    "provider":"ollama","model":model_override or "local-model",
                    "content":"AI Demo is running normally.",
                    "tool_calls":[],
                    "usage":{"prompt_tokens":12,"completion_tokens":6,"total_tokens":18},
                }
            def fail_cloud(*args,**kwargs):
                raise AssertionError("Cloud provider must not be used while app cloud policy is disabled.")
            providers.generate_ollama=fake_local
            providers.generate=fail_cloud

            result=client.post("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/prompt",json={
                "prompt":"What is the current status?",
                "context_keys":["status"],
            })
            assert result.status_code==200,result.text
            body=result.json()
            assert body["compute_source"]=="homeserver_local"
            assert body["provider_key"]=="ollama"
            assert body["model"]=="local-model"
            assert body["provider_secrets_exposed"] is False
            assert len(calls)==1
            assert "Authorized app context" in calls[0]["messages"][0]["content"]

            job=homeserver_app_runtime.run_job("ai.demo","daily-summary")
            assert job["status"]=="succeeded"
            assert job["output"]["compute_source"]=="homeserver_local"
            assert job["output"]["provider_key"]=="ollama"
            assert job["output"]["content"]=="AI Demo is running normally."

            runs=client.get("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/runs").json()
            assert runs["count"]>=2
            assert all("content" not in row for row in runs["runs"])
            assert all("messages" not in row for row in runs["runs"])
            assert all("secret" not in str(row).lower() for row in runs["runs"])

            usage_rows=usage.list_usage(source_app_key="app:ai.demo",limit=20)
            assert len(usage_rows)>=2
            assert all(row["compute_source"]=="homeserver_local" for row in usage_rows)
            assert sum(row["total_tokens"] for row in usage_rows)>=36

            brain=client.get("/api/v1/control/homeserver-apps/agent-runtime/brain-context").json()
            assert brain["contract"]=="vp3.app.agent-runtime.brain-context.v1"
            ai_demo=next(row for row in brain["apps"] if row["app_key"]=="ai.demo")
            assert ai_demo["cloud_allowed"] is False
            assert brain["governance"]["apps_receive_provider_secrets"] is False
            assert brain["governance"]["home_server_brokers_inference"] is True
            assert "AI Demo is running normally" not in str(brain)

            updated=client.put("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/policy",json={
                "values":{"cloud_allowed":True,"max_daily_requests":5,"max_prompt_chars":5000,"max_context_chars":2000}
            })
            assert updated.status_code==200,updated.text
            assert updated.json()["policy"]["cloud_allowed"] is True

            cloud_calls=[]
            def fake_cloud(messages,model_override=None,**kwargs):
                cloud_calls.append(model_override)
                return {
                    "provider":"openai","model":"cloud-model",
                    "content":"Cloud-brokered response.",
                    "tool_calls":[],
                    "usage":{"prompt_tokens":8,"completion_tokens":4,"total_tokens":12},
                }
            providers.generate=fake_cloud
            cloud=client.post("/api/v1/control/homeserver-apps/ai.demo/agent-runtime/prompt",json={
                "prompt":"Use the configured route.",
                "context_keys":[],
            })
            assert cloud.status_code==200,cloud.text
            assert cloud.json()["compute_source"]=="user_provider"
            assert cloud.json()["provider_key"]=="openai"
            assert cloud_calls==["cloud-model"]
        finally:
            providers.inference_status=original_status
            providers.generate_ollama=original_generate_ollama
            providers.generate=original_generate

        capability=client.get("/api/v1/control/homeserver-apps/agent-runtime/capability").json()
        assert capability["brokered_inference"] is True
        assert capability["apps_receive_provider_secrets"] is False
        assert capability["background_agent_jobs"] is True
        assert capability["default_cloud_allowed"] is False

print("HomeServer Section 24 Local AI / Agent Runtime: PASS")

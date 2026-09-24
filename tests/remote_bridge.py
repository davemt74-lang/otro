from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-remote-bridge-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import remote_bridge  # noqa: E402
    from app.services.remote_identity import load_or_create_remote_identity, remote_identity_metadata  # noqa: E402

    assert settings.version == "2.3"
    assert remote_bridge.normalize_broker_url("wss://bridge.example.test/homeserver") == "wss://bridge.example.test/homeserver"
    assert remote_bridge.normalize_broker_url("ws://127.0.0.1:8765/bridge") == "ws://127.0.0.1:8765/bridge"
    for invalid in (
        "http://bridge.example.test",
        "ws://bridge.example.test/bridge",
        "wss://user:pass@bridge.example.test/bridge",
        "wss://bridge.example.test/bridge?secret=value",
        "wss://bridge.example.test/bridge#fragment",
    ):
        try:
            remote_bridge.normalize_broker_url(invalid)
            raise AssertionError(f"Expected broker URL rejection: {invalid}")
        except remote_bridge.RemoteBridgeError:
            pass

    identity = load_or_create_remote_identity()
    identity_again = load_or_create_remote_identity()
    assert identity["device_id"] == identity_again["device_id"]
    assert identity["device_secret"] == identity_again["device_secret"]
    metadata = remote_identity_metadata()
    assert "device_secret" not in metadata
    assert metadata["device_id"] == identity["device_id"]
    if os.name == "nt":
        assert identity["device_secret"].encode("utf-8") not in settings.remote_bridge_secret_path.read_bytes()
        assert metadata["protection"] == "windows-dpapi"

    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload or {"ok": True}

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append({"init": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path, **kwargs):
            calls.append({"method": "GET", "path": path, **kwargs})
            return FakeResponse(payload={"path": path})

        def post(self, path, **kwargs):
            calls.append({"method": "POST", "path": path, **kwargs})
            return FakeResponse(payload={"path": path})

    original_client = remote_bridge.httpx.Client
    remote_bridge.httpx.Client = FakeClient
    try:
        capabilities = remote_bridge.dispatch_remote_request("capabilities", {})
        assert capabilities["ok"] is True
        assert calls[-1]["path"] == "/api/v1/capabilities"

        pair_request = remote_bridge.dispatch_remote_request(
            "pair.request",
            {"app_key": "remote-test", "app_name": "Remote Test", "permissions": ["agent.chat"]},
        )
        assert pair_request["ok"] is True
        assert calls[-1]["path"] == "/api/v1/pairing/request"

        try:
            remote_bridge.dispatch_remote_request("agent.chat", {"message": "test"})
            raise AssertionError("Protected remote operation accepted without bearer token")
        except remote_bridge.RemoteBridgeError:
            pass

        token = "synthetic_remote_bearer_token_" + "x" * 32
        chat = remote_bridge.dispatch_remote_request("agent.chat", {"message": "synthetic message"}, token)
        assert chat["ok"] is True
        assert calls[-1]["path"] == "/api/v1/chat"
        assert calls[-1]["headers"]["Authorization"] == f"Bearer {token}"

        legacy_chat = remote_bridge.dispatch_remote_request("chat", {"message": "legacy synthetic message"}, token)
        assert legacy_chat["ok"] is True
        assert calls[-1]["path"] == "/api/v1/chat"

        conversation_id = "550e8400-e29b-41d4-a716-446655440000"
        conversation = remote_bridge.dispatch_remote_request(
            "conversation.get", {"conversation_id": conversation_id}, token
        )
        assert conversation["ok"] is True
        assert calls[-1]["path"] == f"/api/v1/conversations/{conversation_id}"

        inference = remote_bridge.dispatch_remote_request("inference.status", {}, token)
        assert inference["ok"] is True
        assert calls[-1]["path"] == "/api/v1/inference/status"

        fleet_status = remote_bridge.dispatch_remote_request(
            "fleet.device.status", {}, token
        )
        assert fleet_status["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/device/status"

        fleet_diagnostics = remote_bridge.dispatch_remote_request(
            "fleet.device.diagnostics", {}, token
        )
        assert fleet_diagnostics["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/device/diagnostics"
        assert calls[-1]["method"] == "POST"

        fleet_support = remote_bridge.dispatch_remote_request(
            "fleet.device.support_summary", {}, token
        )
        assert fleet_support["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/device/support-summary"

        fleet_update = remote_bridge.dispatch_remote_request(
            "fleet.device.update_request",
            {
                "request_key": "remote-fleet-update-001",
                "package_sha256": "c" * 64,
                "release_version": "v1.2",
            },
            token,
        )
        assert fleet_update["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/device/update-requests"

        fleet_inventory = remote_bridge.dispatch_remote_request(
            "fleet.inventory", {"limit": 25}, token
        )
        assert fleet_inventory["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/inventory"
        assert calls[-1]["params"] == {"limit": 25}

        fleet_rollouts = remote_bridge.dispatch_remote_request(
            "fleet.rollouts", {"limit": 12}, token
        )
        assert fleet_rollouts["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/rollouts"
        assert calls[-1]["params"] == {"limit": 12}

        fleet_outcome = remote_bridge.dispatch_remote_request(
            "fleet.rollout.outcome",
            {
                "rollout_id": 7,
                "device_id": "hs-" + "d" * 24,
                "outcome": "healthy",
                "detail_code": "health_check_passed",
            },
            token,
        )
        assert fleet_outcome["ok"] is True
        assert calls[-1]["path"] == "/api/v1/fleet/rollouts/7/outcomes"
        assert calls[-1]["json"]["outcome"] == "healthy"

        emitted = remote_bridge.dispatch_remote_request(
            "events.emit",
            {"event_id": "remote-event-1", "event_type": "campaign.claimed", "summary": "Claimed"},
            token,
        )
        assert emitted["ok"] is True
        assert calls[-1]["path"] == "/api/v1/events"
        assert calls[-1]["method"] == "POST"

        events = remote_bridge.dispatch_remote_request(
            "events.list", {"limit": 25, "event_type": "campaign.claimed"}, token
        )
        assert events["ok"] is True
        assert calls[-1]["path"] == "/api/v1/events"
        assert calls[-1]["params"] == {"limit": 25, "event_type": "campaign.claimed"}

        awareness = remote_bridge.dispatch_remote_request("awareness.list", {"limit": 12}, token)
        assert awareness["ok"] is True
        assert calls[-1]["path"] == "/api/v1/awareness"
        assert calls[-1]["params"] == {"limit": 12}

        plugin_list = remote_bridge.dispatch_remote_request("plugins.list", {}, token)
        assert plugin_list["ok"] is True
        assert calls[-1]["path"] == "/api/v1/plugins"

        usage_write = remote_bridge.dispatch_remote_request(
            "usage.write",
            {"event_id": "charge-1", "billable_tokens": 20, "total_tokens": 10},
            token,
        )
        assert usage_write["ok"] is True
        assert calls[-1]["path"] == "/api/v1/usage/cloud"

        legacy_usage_write = remote_bridge.dispatch_remote_request(
            "usage.cloud",
            {"event_id": "charge-legacy", "billable_tokens": 20, "total_tokens": 10},
            token,
        )
        assert legacy_usage_write["ok"] is True
        assert calls[-1]["path"] == "/api/v1/usage/cloud"

        usage_read = remote_bridge.dispatch_remote_request("usage.read", {"limit": 40}, token)
        assert usage_read["ok"] is True
        assert calls[-1]["path"] == "/api/v1/usage"
        assert calls[-1]["params"] == {"limit": 40}

        tool = remote_bridge.dispatch_remote_request(
            "tool.execute",
            {"tool_key": "knowledge.search", "arguments": {"query": "synthetic query"}},
            token,
        )
        assert tool["ok"] is True
        assert calls[-1]["path"] == "/api/v1/tools/knowledge.search/execute"

        for operation, payload in (
            ("owner.control", {}),
            ("http.proxy", {"url": "http://127.0.0.1:4377/api/v1/control/system"}),
            ("conversation.get", {"conversation_id": "../../system"}),
            ("events.list", {"event_type": "../../control"}),
            ("awareness.list", {"limit": 1000}),
            ("tool.execute", {"tool_key": "../../control", "arguments": {}}),
            ("fleet.rollout.outcome", {"rollout_id": 0, "device_id": "hs-" + "e" * 24, "outcome": "healthy"}),
        ):
            try:
                remote_bridge.dispatch_remote_request(operation, payload, token)
                raise AssertionError(f"Remote operation should be rejected: {operation}")
            except remote_bridge.RemoteBridgeError:
                pass
    finally:
        remote_bridge.httpx.Client = original_client

    with TestClient(app) as client:
        assert client.get("/remote").status_code == 401
        assert client.get("/api/v1/control/remote-bridge").status_code == 401
        assert client.get("/api/v1/control/cloud-connection").status_code == 401
        bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert bootstrap.status_code == 200
        workspace = client.get("/remote")
        assert workspace.status_code == 200
        status = client.get("/api/v1/control/remote-bridge")
        assert status.status_code == 200
        payload = status.json()
        assert payload["settings"]["enabled"] is False
        assert payload["settings"]["broker_url"] == ""
        assert payload["trust_model"] == "trusted-wss-relay"
        assert payload["end_to_end_payload_encryption"] is False
        assert "device_secret" not in json.dumps(payload)

        cloud_status = client.get("/api/v1/control/cloud-connection")
        assert cloud_status.status_code == 200
        cloud_payload = cloud_status.json()
        assert cloud_payload["service"]["version"] == settings.version
        assert cloud_payload["cloud"]["connected"] is False
        assert cloud_payload["cloud"]["state"] == "not_connected"
        assert cloud_payload["cloud"]["transport_label"] in {"VP3 HTTPS Relay", "Custom WebSocket Relay"}

        invalid = client.put(
            "/api/v1/control/remote-bridge",
            json={"enabled": True, "broker_url": "ws://bridge.example.test/relay"},
        )
        assert invalid.status_code == 422

        configured = client.put(
            "/api/v1/control/remote-bridge",
            json={"enabled": True, "broker_url": "ws://127.0.0.1:48765/relay"},
        )
        assert configured.status_code == 200
        assert configured.json()["settings"]["enabled"] is True

        pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "remote-boundary", "app_name": "Remote Boundary", "permissions": ["agent.chat"]},
        )
        pair_json = pair.json()
        assert client.post("/api/v1/pairing/approve", json={"code": pair_json["code"]}).status_code == 200
        token = pair_json["claim_token"]

        with TestClient(app) as paired:
            assert paired.get(
                "/api/v1/control/remote-bridge",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code == 401
            assert paired.get("/remote", headers={"Authorization": f"Bearer {token}"}).status_code == 401

print("HomeServer v0.18 remote bridge security test passed")

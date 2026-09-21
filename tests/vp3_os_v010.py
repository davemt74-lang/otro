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


with tempfile.TemporaryDirectory(prefix="vp3-os-v010-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_node"
    os.environ["VP3_OS_HARDWARE_REVISION"] = "node-devkit-a"

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers, remote_bridge, vp3_os  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    original_inference_status = providers.inference_status

    providers.inference_status = lambda: {
        "available": False,
        "selected_provider": "",
        "model": "",
        "compute_source": "",
        "cloud_fallback_required": True,
        "providers": [],
    }

    try:
        with TestClient(app) as client:
            scheduler.stop()
            vp3_os.clear_reported_hardware()

            # Public discovery identifies the OS/profile but must not disclose a
            # stable device identity before pairing.
            public = client.get("/api/v1/capabilities")
            assert public.status_code == 200, public.text
            public_os = public.json()["vp3_os"]
            assert public_os["platform"] == "VP3 OS"
            assert public_os["platform_version"] == "v0.10"
            assert public_os["profile"]["key"] == "vp3_node"
            assert public_os["profile"]["label"] == "VP3 Node"
            assert "device_id" not in public_os
            assert "vp3.os.v010" in public.json()["features"]
            assert "vp3.os.hardware_profiles.v1" in public.json()["features"]
            assert "vp3.os.placement.v1" in public.json()["features"]

            # Owner hardware state remains behind the existing owner gateway.
            assert client.get("/api/v1/control/vp3-os").status_code == 401
            assert client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            ).status_code == 200

            owner = client.get("/api/v1/control/vp3-os")
            assert owner.status_code == 200, owner.text
            owner_os = owner.json()
            assert owner_os["device_id"].startswith("hs-")
            assert owner_os["hardware_revision"] == "node-devkit-a"
            assert owner_os["profile"]["expected_hardware"] == [
                "agent_button",
                "status_light",
                "microphone",
                "speaker",
                "privacy_switch",
            ]
            # v0.10 never guesses hardware presence from a product profile.
            assert owner_os["hardware"]["microphone"]["present"] is False
            assert owner_os["hardware"]["privacy_switch"]["physical_disconnect"] is False
            assert owner_os["privacy"]["raw_audio_cloud_default"] is False
            assert owner_os["authority"]["agent_brain"] == "vp3_os_existing"
            assert owner_os["authority"]["hardware_io"] == "vp3_os_hardware_adapters_v020"

            unavailable = client.post(
                "/api/v1/control/vp3-os/placement",
                json={
                    "raw_audio": True,
                    "hardware": ["microphone"],
                    "cloud_ready": True,
                },
            )
            assert unavailable.status_code == 200, unavailable.text
            assert unavailable.json()["placement"] == "DEFER"
            assert unavailable.json()["reason"] == "required_hardware_unavailable"
            assert unavailable.json()["raw_audio_cloud_allowed"] is False

            # Hardware adapters report normalized state. A physical privacy
            # switch is explicitly reported; profile selection alone never
            # makes that claim.
            vp3_os.report_hardware_state("microphone", present=True, ready=True)
            vp3_os.report_hardware_state(
                "privacy_switch",
                present=True,
                ready=True,
                metadata={
                    "physical_disconnect": True,
                    "microphone_powered": True,
                },
            )
            owner_ready = client.get("/api/v1/control/vp3-os").json()
            assert owner_ready["hardware"]["microphone"]["ready"] is True
            assert owner_ready["privacy"]["physical_microphone_disconnect_reported"] is True

            raw_audio = client.post(
                "/api/v1/control/vp3-os/placement",
                json={
                    "raw_audio": True,
                    "hardware": ["microphone"],
                    "cloud_ready": True,
                    "allow_cloud": True,
                },
            ).json()
            assert raw_audio["placement"] == "LOCAL_ONLY"
            assert raw_audio["reason"] == "raw_audio_local_only"
            assert raw_audio["cloud_ready"] is True
            assert raw_audio["raw_audio_cloud_allowed"] is False

            secret = client.post(
                "/api/v1/control/vp3-os/placement",
                json={"sensitivity": "secret", "cloud_ready": True},
            ).json()
            assert secret["placement"] == "LOCAL_ONLY"
            assert secret["reason"] == "secret_local_only"

            # Pair a cloud application through the existing HomeServer trust
            # model. The paired surface gets the opaque bridge identity but no
            # detailed hardware inventory.
            request = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "vp3-os-test",
                    "app_name": "VP3 OS Test",
                    "permissions": ["agent.chat"],
                },
            ).json()
            approved = client.post(
                "/api/v1/pairing/approve",
                json={"code": request["code"]},
            )
            assert approved.status_code == 200, approved.text
            headers = {"Authorization": f"Bearer {request['claim_token']}"}

            assert client.get("/api/v1/vp3-os/status").status_code == 401
            paired = client.get("/api/v1/vp3-os/status", headers=headers)
            assert paired.status_code == 200, paired.text
            paired_json = paired.json()
            assert paired_json["app"] == "vp3-os-test"
            assert paired_json["device_id"].startswith("hs-")
            assert "hardware" not in paired_json

            registry = client.get("/api/v1/capability-registry", headers=headers)
            assert registry.status_code == 200, registry.text
            registry_json = registry.json()
            assert registry_json["vp3_os"]["platform_version"] == "v0.10"
            assert registry_json["vp3_os"]["device_id"] == paired_json["device_id"]
            assert "vp3.os.status" in registry_json["operations"]
            assert "vp3.os.placement" in registry_json["operations"]

            # Cloud allowance is narrowed by the existing app-scope contract.
            apps = client.get("/api/v1/control/apps").json()["apps"]
            app_id = next(item["id"] for item in apps if item["app_key"] == "vp3-os-test")
            scope = {
                "cloud_allowed": False,
                "memory_key_prefixes": [],
                "knowledge_kinds": [],
                "tool_names": [],
                "plugin_keys": [],
            }
            scoped = client.put(f"/api/v1/control/apps/{app_id}/scope", json=scope)
            assert scoped.status_code == 200, scoped.text

            blocked_cloud = client.post(
                "/api/v1/vp3-os/placement",
                headers=headers,
                json={"needs_cloud_model": True, "cloud_ready": True},
            )
            assert blocked_cloud.status_code == 200, blocked_cloud.text
            assert blocked_cloud.json()["cloud_allowed"] is False
            assert blocked_cloud.json()["placement"] == "DEFER"
            assert blocked_cloud.json()["reason"] == "cloud_disallowed"

            # A ready local model can satisfy the same work without widening the
            # app's cloud scope.
            providers.inference_status = lambda: {
                "available": True,
                "selected_provider": "ollama",
                "model": "local-test",
                "compute_source": "homeserver_local",
                "cloud_fallback_required": False,
                "providers": [],
            }
            local_fallback = client.post(
                "/api/v1/vp3-os/placement",
                headers=headers,
                json={"needs_cloud_model": True, "cloud_ready": True},
            ).json()
            assert local_fallback["cloud_allowed"] is False
            assert local_fallback["local_compute_ready"] is True
            assert local_fallback["placement"] == "LOCAL"
            assert local_fallback["reason"] == "cloud_unavailable_local_compute"

            # Remote Bridge uses the same bearer-authenticated loopback API.
            class FakeResponse:
                status_code = 200

                def __init__(self, payload):
                    self._payload = payload

                def json(self):
                    return self._payload

            calls: list[tuple] = []

            class FakeHttpClient:
                def __init__(self, *args, **kwargs):
                    self.base_url = kwargs.get("base_url")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def get(self, path: str, *, headers: dict | None = None, **kwargs):
                    calls.append(("GET", path, headers, None))
                    return FakeResponse({"platform": "VP3 OS"})

                def post(self, path: str, *, json: dict | None = None, headers: dict | None = None, **kwargs):
                    calls.append(("POST", path, headers, json))
                    return FakeResponse({"placement": "LOCAL"})

            original_http_client = remote_bridge.httpx.Client
            remote_bridge.httpx.Client = FakeHttpClient
            try:
                token = "t" * 24
                relayed_status = remote_bridge.dispatch_remote_request("vp3.os.status", {}, token)
                assert relayed_status["ok"] is True
                assert calls[-1] == (
                    "GET",
                    "/api/v1/vp3-os/status",
                    {"Authorization": f"Bearer {token}"},
                    None,
                )
                relayed_plan = remote_bridge.dispatch_remote_request(
                    "vp3.os.placement",
                    {"needs_cloud_model": False},
                    token,
                )
                assert relayed_plan["ok"] is True
                assert calls[-1][0:3] == (
                    "POST",
                    "/api/v1/vp3-os/placement",
                    {"Authorization": f"Bearer {token}"},
                )
                assert calls[-1][3] == {"needs_cloud_model": False}
            finally:
                remote_bridge.httpx.Client = original_http_client

            encoded = json.dumps(registry_json).lower()
            for forbidden in ("device_secret", "owner-bootstrap", "provider-credentials", "remote-bridge.dat"):
                assert forbidden not in encoded, forbidden
    finally:
        providers.inference_status = original_inference_status
        os.environ.pop("VP3_OS_HARDWARE_PROFILE", None)
        os.environ.pop("VP3_OS_HARDWARE_REVISION", None)

print("VP3 OS v0.10 hardware platform foundation regression passed")

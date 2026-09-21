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


class FakeSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.flushed = 0
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.writes.append(bytes(payload))
        return len(payload)

    def flush(self) -> None:
        self.flushed += 1

    def close(self) -> None:
        self.closed = True


def hello(*, include_power_sense: bool = True) -> dict:
    capabilities = [
        "status_light",
        "agent_button",
        "privacy_switch",
        "mic_power_cut",
    ]
    if include_power_sense:
        capabilities.append("mic_power_sense")
    return {
        "type": "hello",
        "protocol": "vp3-hw-v1",
        "controller_id": "vp3-node-devkit",
        "firmware": "0.20.0",
        "hardware_revision": "node-devkit-a",
        "components": [
            "agent_button",
            "status_light",
            "microphone",
            "speaker",
            "privacy_switch",
        ],
        "capabilities": capabilities,
    }


def state(seq: int, *, privacy: bool, mic_powered: bool) -> dict:
    return {
        "type": "state",
        "seq": seq,
        "components": {
            "agent_button": {
                "present": True,
                "ready": True,
                "pressed": False,
            },
            "status_light": {
                "present": True,
                "ready": True,
            },
            "microphone": {
                "present": True,
                "ready": not privacy and mic_powered,
            },
            "speaker": {
                "present": True,
                "ready": True,
            },
            "privacy_switch": {
                "present": True,
                "ready": True,
                "engaged": privacy,
                "physical_disconnect": True,
                "microphone_powered": mic_powered,
            },
        },
    }


with tempfile.TemporaryDirectory(prefix="vp3-os-v020-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_node"
    os.environ["VP3_OS_HARDWARE_REVISION"] = "node-devkit-a"
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import hardware_adapters, vp3_os  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    # Protocol parser rejects state before a successful hello.
    isolated = hardware_adapters.HardwareAdapterManager()
    try:
        isolated.handle_message(state(1, privacy=False, mic_powered=True))
        raise AssertionError("state accepted before hardware handshake")
    except hardware_adapters.HardwareAdapterError:
        pass

    malformed = hardware_adapters.normalize_controller_message(
        {"type": "state", "seq": "not-an-int", "components": {}}
    )
    assert malformed["seq"] == 0

    # Missing power-sense support can never verify an engaged physical cut.
    vp3_os.clear_reported_hardware()
    unverified = hardware_adapters.HardwareAdapterManager()
    unverified.handle_message(hello(include_power_sense=False))
    unverified.handle_message(state(1, privacy=True, mic_powered=False))
    assert unverified.status()["state"] == "hardware_fault"
    unverified_privacy = vp3_os.manifest(
        include_hardware=False,
        include_device_id=False,
    )["privacy"]
    assert unverified_privacy["physical_microphone_disconnect_verified"] is False

    # Full dev-kit handshake and normal state.
    vp3_os.clear_reported_hardware()
    manager = hardware_adapters.HardwareAdapterManager()
    accepted_hello = manager.handle_message(hello())
    assert accepted_hello["protocol"] == "vp3-hw-v1"
    assert manager.status()["connected"] is True

    first = manager.handle_message(state(1, privacy=False, mic_powered=True))
    assert first["duplicate"] is False
    inventory = vp3_os.hardware_inventory()
    assert inventory["microphone"]["present"] is True
    assert inventory["microphone"]["ready"] is True
    assert inventory["privacy_switch"]["engaged"] is False
    assert inventory["privacy_switch"]["microphone_powered"] is True

    # Controller reboot/re-handshake invalidates the previous device snapshot
    # until fresh state arrives.
    manager.handle_message(hello())
    rehandshake_inventory = vp3_os.hardware_inventory()
    assert rehandshake_inventory["microphone"]["present"] is False
    assert rehandshake_inventory["status_light"]["ready"] is False
    manager.handle_message(state(1, privacy=False, mic_powered=True))

    duplicate_state = manager.handle_message(state(1, privacy=True, mic_powered=False))
    assert duplicate_state["duplicate"] is True
    assert vp3_os.hardware_inventory()["privacy_switch"]["engaged"] is False

    # State frames are authoritative snapshots. A component declared by the
    # controller but omitted from a later state cannot remain stale/ready.
    partial = manager.handle_message({
        "type": "state",
        "seq": 2,
        "components": {
            "privacy_switch": {
                "present": True,
                "ready": True,
                "engaged": False,
                "physical_disconnect": True,
                "microphone_powered": True,
            }
        },
    })
    assert partial["duplicate"] is False
    partial_inventory = vp3_os.hardware_inventory()
    assert partial_inventory["microphone"]["present"] is False
    assert partial_inventory["microphone"]["ready"] is False
    assert partial_inventory["agent_button"]["present"] is False

    # Restore the complete device snapshot for the remaining journey.
    manager.handle_message(state(3, privacy=False, mic_powered=True))

    # Agent-button events are monotonic and duplicate-safe.
    button = manager.handle_message(
        {"type": "event", "seq": 10, "event": "agent_button", "action": "press"}
    )
    assert button["duplicate"] is False
    duplicate_button = manager.handle_message(
        {"type": "event", "seq": 10, "event": "agent_button", "action": "press"}
    )
    assert duplicate_button["duplicate"] is True
    assert manager.events(limit=10)[-1]["action"] == "press"

    # Engaged + physical cut + post-switch rail OFF is the only verified state.
    private_state = manager.handle_message(state(4, privacy=True, mic_powered=False))
    assert private_state["duplicate"] is False
    private_manifest = vp3_os.manifest(
        include_hardware=True,
        include_device_id=False,
    )
    assert private_manifest["privacy"]["privacy_switch_engaged"] is True
    assert private_manifest["privacy"]["microphone_power_state_known"] is True
    assert private_manifest["privacy"]["physical_microphone_disconnect_reported"] is True
    assert private_manifest["privacy"]["physical_microphone_disconnect_verified"] is True
    assert private_manifest["hardware"]["microphone"]["ready"] is False
    assert manager.status()["state"] == "connected"

    # Raw audio cannot start while the privacy switch makes the mic unavailable.
    blocked_audio = vp3_os.placement_plan(
        {
            "raw_audio": True,
            "hardware": ["microphone"],
        },
        cloud_ready=True,
        cloud_allowed=True,
    )
    assert blocked_audio["placement"] == "DEFER"
    assert blocked_audio["reason"] == "required_hardware_unavailable"
    assert blocked_audio["raw_audio_cloud_allowed"] is False

    # If privacy is engaged but the post-switch rail remains powered, fail closed.
    manager.handle_message(state(5, privacy=True, mic_powered=True))
    assert manager.status()["state"] == "hardware_fault"
    assert "verification failed" in manager.status()["last_error"].lower()
    fault_manifest = vp3_os.manifest(
        include_hardware=True,
        include_device_id=False,
    )
    assert fault_manifest["privacy"]["physical_microphone_disconnect_verified"] is False
    assert fault_manifest["hardware"]["microphone"]["ready"] is False

    # A later healthy state clears the fault.
    manager.handle_message(state(6, privacy=False, mic_powered=True))
    assert manager.status()["state"] == "connected"
    assert manager.status()["last_error"] == ""

    # Status light is the only v0.20 hardware command.
    serial = FakeSerial()
    manager._serial = serial
    light = manager.set_status_light("listening")
    assert light["accepted"] is True
    assert light["mode"] == "listening"
    assert serial.flushed == 1
    wire = json.loads(serial.writes[-1].decode("utf-8"))
    assert wire["command"] == "status_light.set"
    assert wire["args"] == {"mode": "listening"}

    try:
        manager.set_status_light("microphone_on")
        raise AssertionError("unsafe/unknown status-light mode accepted")
    except hardware_adapters.HardwareAdapterError:
        pass

    # Reference firmware contract: controller reports physical privacy state but
    # exposes no command that can energize microphone power.
    firmware = (
        ROOT_DIR
        / "hardware"
        / "esp32"
        / "vp3_node_controller"
        / "vp3_node_controller.ino"
    ).read_text(encoding="utf-8")
    for required in (
        "vp3-hw-v1",
        "mic_power_cut",
        "mic_power_sense",
        "status_light.set",
        "agent_button",
        "privacy_switch",
        "PIN_MIC_POWER_SENSE",
    ):
        assert required in firmware, required
    assert '"command":"microphone' not in firmware
    assert "PIN_MIC_POWER_ENABLE" not in firmware

    # API integration uses the canonical global adapter manager.
    hardware_adapters.manager.stop()
    vp3_os.clear_reported_hardware()

    with TestClient(app) as client:
        scheduler.stop()

        public = client.get("/api/v1/capabilities")
        assert public.status_code == 200, public.text
        advertised = public.json()
        assert str(advertised["vp3_os"]["os_version"]).startswith("v0.")
        assert advertised["vp3_os"]["platform_version"] == "v0.10"
        assert advertised["vp3_os_hardware"] == {
            "version": "v0.20",
            "protocol": "vp3-hw-v1",
            "supported": True,
            "owner_managed": True,
        }
        for feature in (
            "vp3.os.v020",
            "vp3.os.hardware_adapters.v1",
            "vp3.os.serial_controller.v1",
            "vp3.os.privacy_power_sense.v1",
        ):
            assert feature in advertised["features"]

        assert client.get("/api/v1/control/vp3-os").status_code == 401
        assert client.get("/api/v1/control/vp3-os/hardware/events").status_code == 401
        assert client.post(
            "/api/v1/control/vp3-os/hardware/status-light",
            json={"mode": "idle"},
        ).status_code == 401

        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        hardware_adapters.manager.handle_message(hello())
        hardware_adapters.manager.handle_message(state(1, privacy=False, mic_powered=True))
        hardware_adapters.manager.handle_message(
            {"type": "event", "seq": 1, "event": "agent_button", "action": "press"}
        )
        fake_serial = FakeSerial()
        hardware_adapters.manager._serial = fake_serial

        owner = client.get("/api/v1/control/vp3-os")
        assert owner.status_code == 200, owner.text
        owner_json = owner.json()
        assert str(owner_json["os_version"]).startswith("v0.")
        assert owner_json["hardware_adapter"]["version"] == "v0.20"
        assert owner_json["hardware_adapter"]["controller"]["controller_id"] == "vp3-node-devkit"

        events = client.get("/api/v1/control/vp3-os/hardware/events?limit=10")
        assert events.status_code == 200, events.text
        assert events.json()["items"][-1]["event"] == "agent_button"

        light_api = client.post(
            "/api/v1/control/vp3-os/hardware/status-light",
            json={"mode": "thinking"},
        )
        assert light_api.status_code == 200, light_api.text
        assert light_api.json()["mode"] == "thinking"
        light_wire = json.loads(fake_serial.writes[-1].decode("utf-8"))
        assert light_wire["command"] == "status_light.set"

        invalid_light = client.post(
            "/api/v1/control/vp3-os/hardware/status-light",
            json={"mode": "microphone_on"},
        )
        assert invalid_light.status_code == 422

        # Pairing reuses existing HomeServer/VP3 trust; paired views are
        # sanitized and never reveal local serial configuration/controller ID.
        request = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-hardware-test",
                "app_name": "VP3 Hardware Test",
                "permissions": ["agent.chat"],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve",
            json={"code": request["code"]},
        ).status_code == 200
        headers = {"Authorization": f"Bearer {request['claim_token']}"}

        paired = client.get("/api/v1/vp3-os/status", headers=headers)
        assert paired.status_code == 200, paired.text
        paired_json = paired.json()
        adapter = paired_json["hardware_adapter"]
        assert adapter["version"] == "v0.20"
        assert adapter["connected"] is True
        assert adapter["controller"]["firmware"] == "0.20.0"
        assert "controller_id" not in adapter["controller"]
        paired_encoded = json.dumps(paired_json).lower()
        for forbidden in ("configured_port", "com7", "device_secret", "remote-bridge.dat"):
            assert forbidden not in paired_encoded, forbidden

        registry = client.get("/api/v1/capability-registry", headers=headers)
        assert registry.status_code == 200, registry.text
        registry_json = registry.json()
        assert str(registry_json["vp3_os"]["os_version"]).startswith("v0.")
        assert registry_json["vp3_os_hardware"]["version"] == "v0.20"
        assert "controller_id" not in json.dumps(registry_json["vp3_os_hardware"]).lower()

    vp3_os.clear_reported_hardware()
    os.environ.pop("VP3_OS_HARDWARE_PROFILE", None)
    os.environ.pop("VP3_OS_HARDWARE_REVISION", None)
    os.environ.pop("VP3_OS_HARDWARE_ADAPTER", None)

print("VP3 OS v0.20 hardware adapter regression passed")

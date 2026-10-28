#!/usr/bin/env python3
"""Regression checks for isolated VP3 wearable v0.1 prototype.

Run from repository root: python tests/vp3_wearable_prototype_v01.py
No device, cloud credentials, or HomeServer service is required.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEARABLE = ROOT / "hardware" / "esp32" / "vp3_wearable_prototype"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WEARABLE))


class FakeSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.flushes = 0

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def flush(self) -> None:
        self.flushes += 1


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="vp3-wearable-") as data:
        os.environ["HOMESERVER_DATA_DIR"] = data
        os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

        from app.services import hardware_adapters, vp3_os
        from simulator_bridge import SimulatedDevice

        simulated = SimulatedDevice()
        hello = simulated._hello()
        assert hello["protocol"] == hardware_adapters.HARDWARE_PROTOCOL
        assert set(hello["components"]) == {"agent_button", "status_light", "display"}
        assert "microphone" not in hello["components"]
        assert "mic_power_cut" not in hello["capabilities"]
        assert "mic_power_sense" not in hello["capabilities"]

        vp3_os.clear_reported_hardware()
        manager = hardware_adapters.HardwareAdapterManager()
        try:
            manager.handle_message(simulated._state())
            raise AssertionError("state before hello must fail")
        except hardware_adapters.HardwareAdapterError:
            pass

        accepted = manager.handle_message(hello)
        assert accepted["controller_id"] == simulated.snapshot()["controller"]
        ready = manager.handle_message(simulated._state())
        assert ready["duplicate"] is False
        inventory = vp3_os.hardware_inventory()
        assert inventory["agent_button"]["ready"] is True
        assert inventory["display"]["ready"] is True
        assert inventory["status_light"]["ready"] is True
        assert inventory["microphone"]["ready"] is False
        assert inventory["privacy_switch"]["ready"] is False

        for action in ("press", "hold", "release"):
            evt = simulated.touch(action)
            result = manager.handle_message(evt)
            assert result["duplicate"] is False
            assert manager.events(limit=1)[-1]["action"] == action
            manager.handle_message(simulated._state())
        assert simulated.snapshot()["pressed"] is False

        evt = simulated.touch("press")
        assert manager.handle_message(evt)["duplicate"] is False
        assert manager.handle_message(evt)["duplicate"] is True
        manager.handle_message(simulated._state())
        assert vp3_os.hardware_inventory()["agent_button"]["pressed"] is True
        manager.handle_message(simulated.touch("release"))
        manager.handle_message(simulated._state())

        serial = FakeSerial()
        manager._serial = serial
        light = manager.set_status_light("thinking")
        assert light["accepted"] is True
        assert serial.flushes == 1
        command = json.loads(serial.writes[-1].decode("utf-8"))
        assert command["command"] == "status_light.set"
        assert command["args"] == {"mode": "thinking"}
        ack = simulated.set_mode("thinking")
        assert manager.handle_message(ack)["type"] == "ack"
        assert ack["ok"] is True
        assert simulated.snapshot()["mode"] == "thinking"

        for invalid in ("microphone_on", "secret", ""):
            try:
                simulated.set_mode(invalid)
                raise AssertionError("invalid light mode accepted")
            except ValueError:
                pass
        try:
            simulated.touch("enable_mic")
            raise AssertionError("unsupported button event accepted")
        except ValueError:
            pass

        privacy = vp3_os.manifest(
            include_hardware=False, include_device_id=False
        )["privacy"]
        assert privacy["physical_microphone_disconnect_verified"] is False
        audio = vp3_os.placement_plan(
            {"raw_audio": True, "hardware": ["microphone"]},
            cloud_ready=True, cloud_allowed=True,
        )
        assert audio["placement"] == "DEFER"
        assert audio["reason"] == "required_hardware_unavailable"

        # Handshake recovery keeps the original controller's dedup semantics.
        simulated.connect()
        manager.handle_message(simulated._hello())
        assert vp3_os.hardware_inventory()["display"]["ready"] is False
        manager.handle_message(simulated._state())
        assert vp3_os.hardware_inventory()["display"]["ready"] is True
        assert manager.handle_message(simulated.touch("press"))["duplicate"] is False

        # The firmware must advertise only safe non-audio components.
        firmware = (WEARABLE / "VP3_Wearable.ino").read_text(encoding="utf-8")
        assert r'\"components\":[\"agent_button\",\"status_light\",\"display\"]' in firmware
        for unsafe in (r'\"mic_power_cut\"', r'\"privacy_switch\"', "MIC_Init(", "Audio_Init("):
            assert unsafe not in firmware, unsafe
        for required in ("Lvgl_Init()", "Lvgl_Loop()", "status_light.set", 'sendEvent("hold")'):
            assert required in firmware, required

        # Both preview windows must be served by the same loopback-only bridge.
        # The desktop view controls the same in-memory device as the round UI.
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from simulator_bridge import Handler

        service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{service.server_port}"
            for route, title in (
                ("/", "VP3 Wearable"),
                ("/desktop_agent.html", "Desktop Floating Agent"),
            ):
                with urlopen(url + route, timeout=3) as response:
                    assert response.status == 200
                    assert title in response.read().decode("utf-8")
            req = Request(
                url + "/api/mode",
                data=json.dumps({"mode": "speaking"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=3) as response:
                assert json.loads(response.read())["ok"] is True
            with urlopen(url + "/api/state", timeout=3) as response:
                assert json.loads(response.read())["mode"] == "speaking"

            reconnect = Request(
                url + "/api/reconnect",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(reconnect, timeout=3) as response:
                assert json.loads(response.read())["ok"] is True
            with urlopen(url + "/api/state", timeout=3) as response:
                frames = json.loads(response.read())["frames"]
                assert not any(
                    row["frame"]["type"] == "event"
                    for row in frames if row["direction"] == "device-to-home"
                )

            # A cross-origin page must not be able to send control frames.
            cross_origin = Request(
                url + "/api/mode",
                data=json.dumps({"mode": "listening"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://untrusted.example",
                },
                method="POST",
            )
            try:
                urlopen(cross_origin, timeout=3)
                raise AssertionError("cross-origin state mutation accepted")
            except HTTPError as exc:
                assert exc.code == 403
        finally:
            service.shutdown()
            service.server_close()
            worker.join(timeout=3)

        # Windows launch is an explicit opt-in, not automatic on import.
        launch = (WEARABLE / "START-VP3-PREVIEW.bat").read_text(encoding="utf-8")
        assert "simulator_bridge.py" in launch
        assert "--open" in launch
        assert "python.org/downloads/windows/" in launch
        bridge = (WEARABLE / "simulator_bridge.py").read_text(encoding="utf-8")
        assert 'if "--open" in sys.argv:' in bridge
        assert 'webbrowser.open(f"http://{HOST}:{PORT}/desktop_agent.html")' in bridge

        vp3_os.clear_reported_hardware()
        print("VP3 wearable v0.2 protocol simulator and privacy checks: PASS")


if __name__ == "__main__":
    run()

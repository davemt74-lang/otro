from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temp = tempfile.TemporaryDirectory(prefix="homeserver-section15-")
os.environ["HOMESERVER_DATA_DIR"] = temp.name
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_node"
from app.database import db, initialize_database
from app.services import approvals, hardware_adapters, room_device_automation as devices, vp3_os

initialize_database()
calls = []
def driver(device, command, arguments):
    calls.append(device["provider_device_id"])
    return {"state": {"power": command}}
def reset():
    devices.upsert_room("living", "Living")
    devices.upsert_provider("local", "Local", "test", executable=True)
    devices.register_driver("local", driver)
    return devices.upsert_device("lamp", "local", "physical-a", "Lamp", "light",
        room_key="living", controllable=True, state={"power": "off"})
def propose():
    return approvals.create_device_command_request("owner", {
        "device_key": "lamp", "command": "on", "arguments": {}
    }, owner=True)["result"]["request_id"]
def reject(call, code):
    try:
        call()
    except (approvals.ApprovalError, devices.RoomDeviceError) as exc:
        assert exc.status_code == code, (exc, exc.status_code)
        assert "PRIVATE_PROVIDER_ERROR" not in str(exc)
        return
    raise AssertionError("Expected command rejection")
def count_requests():
    with db() as connection:
        return connection.execute("SELECT COUNT(*) FROM action_requests").fetchone()[0]

# Disabled rooms block both new requests and commands approved earlier.
reset(); pending = propose()
devices.upsert_room("living", "Living", enabled=False)
assert not devices.get_device("lamp")["currently_executable"]
assert devices.get_device("lamp")["execution_blocked_reason"] == "Room disabled"
reject(propose, 409); reject(lambda: approvals.approve_request(pending), 409)
assert not calls

# Retargeting a stable key, relocating its room, or changing provider type
# cannot redirect a previously approved physical command.
for mutation in ("physical_target", "room", "provider_type", "provider_configuration", "device_configuration"):
    reset(); pending = propose()
    if mutation == "physical_target":
        devices.upsert_device("lamp", "local", "physical-b", "Lamp", "light",
            room_key="living", controllable=True)
    elif mutation == "room":
        devices.upsert_room("other", "Other")
        devices.upsert_device("lamp", "local", "physical-a", "Lamp", "light",
            room_key="other", controllable=True)
    elif mutation == "provider_type":
        devices.upsert_provider("local", "Local", "another-driver", executable=True)
    elif mutation == "provider_configuration":
        devices.upsert_provider("local", "Local", "test", executable=True, metadata={"endpoint": "another-gateway"})
    else:
        devices.upsert_device("lamp", "local", "physical-a", "Lamp", "light",
            room_key="living", controllable=True, metadata={"channel": "another-output"})
    reject(lambda: approvals.approve_request(pending), 409)
assert not calls

# Old unbound approvals and an expiry during dispatch cannot release a driver.
reset(); pending = propose()
with db() as connection:
    connection.execute("UPDATE action_requests SET arguments_meta_json='{}' WHERE id=?", (pending,))
reject(lambda: approvals.approve_request(pending), 409)
reset(); pending = propose()
claimed = approvals._reserve_request(approvals._request_for_owner(pending))
with db() as connection:
    connection.execute("UPDATE action_requests SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (pending,))
reject(lambda: devices.execute_command("lamp", "on", {}, source_app_key="owner", action_request_id=pending), 409)
assert not calls

# Source, device and provider authority are still checked at dispatch.
reset(); pending = propose()
approvals._reserve_request(approvals._request_for_owner(pending))
reject(lambda: devices.execute_command("lamp", "on", {}, source_app_key="app:other", action_request_id=pending), 403)
reset(); pending = propose()
devices.upsert_provider("local", "Local", "test", executable=True, status="disconnected")
reject(lambda: approvals.approve_request(pending), 503)
assert not calls

# Invalid numeric commands cannot create approvals or dispatch hardware.
reset()
thermostat = devices.upsert_device("thermostat", "local", "physical-thermostat", "Thermostat", "thermostat",
    room_key="living", controllable=True)
before = count_requests()
for value in ("nan", "Infinity", "-Infinity", float("nan"), float("inf"), 49, 91):
    reject(lambda: approvals.create_device_command_request("owner", {
        "device_key": "thermostat", "command": "set_temperature", "arguments": {"temperature_f": value}
    }, owner=True), 422)
for value in (float("inf"), float("nan")):
    reject(lambda: devices.normalize_command(devices.get_device("lamp"), "set_brightness", {"brightness": value}), 422)
assert devices.normalize_command(thermostat, "set_temperature", {"temperature_f": "72.5"}) == ("set_temperature", {"temperature_f": 72.5})
assert count_requests() == before and not calls

# A negative, queued-only, malformed or unsafe driver acknowledgement never
# becomes a successful action and never publishes provider exception content.
for result in ({"ok": False, "state": {"power": "on"}}, {"ok": "false"},
               {"accepted": True}, {}, {"state": []}, {"state": None}, {"state": {"value": float("nan")}}):
    reset(); pending = propose()
    devices.register_driver("local", lambda *_: result)
    reject(lambda: approvals.approve_request(pending), 502)
    assert devices.get_device("lamp")["state"] == {"power": "off"}
    assert approvals._request_for_owner(pending)["status"] == "failed"
def unsafe_driver(*_):
    raise devices.RoomDeviceError("PRIVATE_PROVIDER_ERROR", 502)
reset(); pending = propose(); devices.register_driver("local", unsafe_driver)
reject(lambda: approvals.approve_request(pending), 502)
assert "PRIVATE_PROVIDER_ERROR" not in json.dumps(devices.list_actions())

# Normal approval and duplicate review execute once, against the original ID.
reset(); pending = propose()
assert approvals.approve_request(pending)["status"] == "executed"
reject(lambda: approvals.approve_request(pending), 409)
assert calls == ["physical-a"]

# Concurrent suggestion requests retain one durable approval. Retries after
# completion report its final state; dismissing cannot overwrite that request.
reset()
suggestion = devices.create_suggestion(source_kind="ambient", reason="Room occupied",
    device_key="lamp", command="on")
before = count_requests()
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(lambda _: devices.request_suggestion(suggestion["id"]), range(4)))
ids = {item["result"]["request_id"] for item in results}
assert len(ids) == 1 and count_requests() == before + 1
pending = ids.pop()
reject(lambda: devices.dismiss_suggestion(suggestion["id"]), 409)
assert approvals.approve_request(pending)["status"] == "executed"
replay = devices.request_suggestion(suggestion["id"])
assert replay["result"]["request_id"] == pending and replay["result"]["status"] == "executed"
assert replay["approval_required"] is False and count_requests() == before + 1

# A failure while linking the suggestion rolls back both proposal and audit.
suggestion = devices.create_suggestion(source_kind="ambient", reason="Room occupied",
    device_key="lamp", command="off")
before = count_requests()
with patch.object(devices, "mark_suggestion_requested", side_effect=RuntimeError("link failed")):
    try:
        devices.request_suggestion(suggestion["id"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected link failure")
assert count_requests() == before
assert devices.get_suggestion(suggestion["id"])["status"] == "suggested"
devices.dismiss_suggestion(suggestion["id"])
reject(lambda: devices.request_suggestion(suggestion["id"]), 409)
assert count_requests() == before

# Silent serial controllers disconnect and clear stale hardware readiness.
hello = {"type": "hello", "protocol": "vp3-hw-v1", "controller_id": "section15",
    "components": ["microphone", "privacy_switch"], "capabilities": ["mic_power_cut", "mic_power_sense"]}
state = {"type": "state", "seq": 1, "components": {
    "microphone": {"present": True, "ready": True},
    "privacy_switch": {"present": True, "ready": True, "engaged": False}}}
class SilentSerial:
    def __init__(self, extra=None):
        self.frames = [json.dumps(hello).encode(), json.dumps(state).encode()]
        if extra is not None:
            self.frames.append(json.dumps(extra).encode())
        self.closed = False
    def readline(self, *_): return self.frames.pop(0) if self.frames else b""
    def write(self, data): return len(data)
    def flush(self): pass
    def close(self): self.closed = True
class OneAttempt(hardware_adapters.HardwareAdapterManager):
    def _mark_disconnected(self, error):
        super()._mark_disconnected(error)
        self._stop.set()
for extra in (None, {"type": "ack", "ok": True}, state):
    serial = SilentSerial(extra); manager = OneAttempt()
    with patch.object(manager, "_serial_module", return_value=(SimpleNamespace(Serial=lambda **_: serial), None)), \
         patch.object(manager, "_select_port", return_value="test"), \
         patch.object(hardware_adapters.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 11.0]):
        manager._run_serial()
    assert serial.closed and manager.status()["connected"] is False
    assert "stopped reporting" in manager.status()["last_error"]
    assert vp3_os.hardware_inventory()["microphone"]["ready"] is False
assert hardware_adapters.normalize_controller_message({"type": "ack", "ok": "false"})["ok"] is False
assert hardware_adapters.normalize_controller_message({"type": "ack", "ok": True})["ok"] is True
print("SECTION15_DEVICE_AUTHORITY_RECEIPTS_SUGGESTIONS_AND_SILENT_CONTROLLER=PASS")

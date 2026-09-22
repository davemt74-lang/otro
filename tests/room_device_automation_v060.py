from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="vp3-os-v060-room-device-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import (  # noqa: E402
        action_policy,
        agent_tools,
        room_device_automation,
        tools,
    )
    from app.services.tasks import scheduler  # noqa: E402

    driver_calls: list[dict] = []

    def test_driver(device: dict, command: str, arguments: dict) -> dict:
        driver_calls.append(
            {
                "device_key": device["device_key"],
                "command": command,
                "arguments": dict(arguments),
            }
        )
        state = dict(device.get("state") or {})
        if command == "on":
            state["power"] = "on"
        elif command == "off":
            state["power"] = "off"
        elif command == "toggle":
            state["power"] = "off" if state.get("power") == "on" else "on"
        elif command == "set_brightness":
            state["brightness"] = int(arguments["brightness"])
            state["power"] = "on" if int(arguments["brightness"]) > 0 else "off"
        elif command == "set_temperature":
            state["temperature_f"] = float(arguments["temperature_f"])
        elif command == "set_mode":
            state["mode"] = str(arguments["mode"])
        elif command == "activate":
            state["last_activation"] = "test"
        return {"state": state}

    try:
        with TestClient(app) as client:
            scheduler.stop()

            # Owner control remains protected.
            assert client.get("/api/v1/control/vp3-os/automation").status_code == 401
            assert client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            ).status_code == 200

            # v0.60 migration + public contract.
            with db() as connection:
                assert connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=23"
                ).fetchone() is not None
                for table in (
                    "automation_rooms",
                    "automation_providers",
                    "automation_devices",
                    "automation_device_actions",
                    "automation_suggestions",
                ):
                    assert connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone() is not None

            caps = client.get("/api/v1/capabilities").json()
            assert caps["vp3_os"]["os_version"] == "v0.60"
            automation_caps = caps["vp3_os_room_device_automation"]
            assert automation_caps["version"] == "v0.60"
            assert automation_caps["ambient_direct_execution"] is False
            assert "lock" in automation_caps["discovery_only_categories"]
            assert "light" in automation_caps["safe_control_categories"]
            for feature in (
                "vp3.os.v060",
                "vp3.os.room_device_registry.v1",
                "vp3.os.device_actions.v1",
                "vp3.os.device_suggestions.v1",
                "vp3.os.device_actions.approval_required",
            ):
                assert feature in caps["features"]

            # Register local inventory. The provider may claim execution support,
            # but no command is actually executable until a live in-process
            # driver binds to the same provider key.
            room = client.put(
                "/api/v1/control/vp3-os/automation/rooms/living-room",
                json={
                    "room_key": "living-room",
                    "name": "Living Room",
                    "description": "Main room",
                    "enabled": True,
                },
            )
            assert room.status_code == 200, room.text

            provider = client.put(
                "/api/v1/control/vp3-os/automation/providers/test-local",
                json={
                    "provider_key": "test-local",
                    "name": "Test Local Provider",
                    "provider_type": "test",
                    "enabled": True,
                    "executable": True,
                    "status": "connected",
                    "metadata": {"private_token": "MUST_NOT_LEAK"},
                },
            )
            assert provider.status_code == 200, provider.text
            assert provider.json()["provider"]["currently_executable"] is False

            lamp = client.put(
                "/api/v1/control/vp3-os/automation/devices/living-room-lamp",
                json={
                    "device_key": "living-room-lamp",
                    "provider_key": "test-local",
                    "provider_device_id": "light.floor",
                    "name": "Floor Lamp",
                    "category": "light",
                    "room_key": "living-room",
                    "enabled": True,
                    "controllable": True,
                    "capabilities": {"power": True, "brightness": True},
                    "state": {"power": "off", "brightness": 40},
                    "metadata": {"local_secret": "PRIVATE_DEVICE_META"},
                },
            )
            assert lamp.status_code == 200, lamp.text
            assert lamp.json()["device"]["controllable"] is True
            assert lamp.json()["device"]["currently_executable"] is False

            # High-impact categories can be inventoried but cannot be made
            # controllable in v0.60.
            lock = client.put(
                "/api/v1/control/vp3-os/automation/devices/front-door",
                json={
                    "device_key": "front-door",
                    "provider_key": "test-local",
                    "provider_device_id": "lock.front",
                    "name": "Front Door",
                    "category": "lock",
                    "room_key": "living-room",
                    "enabled": True,
                    "controllable": True,
                    "capabilities": {"lock": True},
                    "state": {"locked": True},
                    "metadata": {},
                },
            )
            assert lock.status_code == 200, lock.text
            assert lock.json()["device"]["controllable"] is False
            assert lock.json()["device"]["currently_executable"] is False

            # Direct physical execution is impossible even for local owner tool
            # calls; the primitive requires a valid approval request ID.
            try:
                tools.execute_tool(
                    "owner",
                    "devices.command",
                    {"device_key": "living-room-lamp", "command": "on", "arguments": {}},
                    set(),
                    owner=True,
                )
                raise AssertionError("direct owner device command unexpectedly executed")
            except tools.ToolError as exc:
                assert exc.status_code == 403
                assert "approved action request" in str(exc)
            assert driver_calls == []

            # Even an internal caller cannot fabricate an approval token.
            try:
                room_device_automation.execute_command(
                    "living-room-lamp",
                    "on",
                    {},
                    source_app_key="owner",
                    action_request_id="fabricated-request",
                )
                raise AssertionError("fabricated approval context unexpectedly executed")
            except room_device_automation.RoomDeviceError as exc:
                assert exc.status_code == 403
            assert driver_calls == []

            # A request can be created before the driver comes online, but
            # approval fails closed and records failure if execution is not
            # currently possible.
            pending_without_driver = client.post(
                "/api/v1/control/vp3-os/automation/devices/living-room-lamp/request",
                json={"command": "on", "arguments": {}},
            )
            assert pending_without_driver.status_code == 200, pending_without_driver.text
            no_driver_id = pending_without_driver.json()["result"]["request_id"]
            failed_approval = client.post(
                f"/api/v1/control/action-requests/{no_driver_id}/approve"
            )
            assert failed_approval.status_code == 503, failed_approval.text
            with db() as connection:
                failed_row = connection.execute(
                    "SELECT status FROM action_requests WHERE id=?",
                    (no_driver_id,),
                ).fetchone()
            assert failed_row["status"] == "failed"
            assert driver_calls == []

            # Bind the local provider driver. Discovery and execution authority
            # remain separate but the lamp is now executable after approval.
            room_device_automation.register_driver("test-local", test_driver)
            overview = client.get("/api/v1/control/vp3-os/automation").json()
            provider_item = next(
                item for item in overview["providers"]
                if item["provider_key"] == "test-local"
            )
            lamp_item = next(
                item for item in overview["devices"]
                if item["device_key"] == "living-room-lamp"
            )
            assert provider_item["driver_registered"] is True
            assert provider_item["currently_executable"] is True
            assert lamp_item["currently_executable"] is True

            # Owner request -> pending approval -> exactly-once physical driver.
            requested = client.post(
                "/api/v1/control/vp3-os/automation/devices/living-room-lamp/request",
                json={"command": "set_brightness", "arguments": {"brightness": 65}},
            )
            assert requested.status_code == 200, requested.text
            request_id = requested.json()["result"]["request_id"]
            assert requested.json()["approval_required"] is True
            assert driver_calls == []

            pending = client.get(
                "/api/v1/control/action-requests?status=pending"
            ).json()["items"]
            item = next(row for row in pending if row["id"] == request_id)
            assert item["action_key"] == "devices.command"
            assert item["arguments"]["device_key"] == "living-room-lamp"
            assert item["arguments"]["command"] == "set_brightness"
            assert item["arguments"]["arguments"] == {"brightness": 65}

            approved = client.post(
                f"/api/v1/control/action-requests/{request_id}/approve"
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["request"]["status"] == "executed"
            assert len(driver_calls) == 1
            assert driver_calls[0] == {
                "device_key": "living-room-lamp",
                "command": "set_brightness",
                "arguments": {"brightness": 65},
            }
            assert client.post(
                f"/api/v1/control/action-requests/{request_id}/approve"
            ).status_code == 409
            assert len(driver_calls) == 1

            after = client.get("/api/v1/control/vp3-os/automation").json()
            updated_lamp = next(
                item for item in after["devices"]
                if item["device_key"] == "living-room-lamp"
            )
            assert updated_lamp["state"]["brightness"] == 65
            assert updated_lamp["state"]["power"] == "on"
            assert after["recent_actions"][0]["status"] == "completed"
            assert after["recent_actions"][0]["command"] == "set_brightness"

            # Invalid/bounded command schemas fail before an approval request
            # can exist.
            invalid = client.post(
                "/api/v1/control/vp3-os/automation/devices/living-room-lamp/request",
                json={"command": "set_brightness", "arguments": {"brightness": 101}},
            )
            assert invalid.status_code == 422

            lock_request = client.post(
                "/api/v1/control/vp3-os/automation/devices/front-door/request",
                json={"command": "unlock", "arguments": {}},
            )
            assert lock_request.status_code == 403

            # Agent tool policy exposes read + proposal, never direct command.
            agent_tools.save_policy(True, 3, True)
            schemas = agent_tools.model_tool_schemas(
                owner=True,
                allow_write_proposals=True,
                source_app_key="owner",
            )
            schema_names = {item["function"]["name"] for item in schemas}
            assert "homeserver_devices_list" in schema_names
            assert "homeserver_device_command_request" in schema_names

            device_read = agent_tools.execute_model_tool(
                "owner",
                "homeserver_devices_list",
                {"room_key": "living-room", "limit": 20},
                set(),
                owner=True,
            )
            assert any(
                row["device_key"] == "living-room-lamp"
                for row in device_read["result"]["items"]
            )
            read_text = json.dumps(device_read, ensure_ascii=False)
            assert "PRIVATE_DEVICE_META" not in read_text
            assert "MUST_NOT_LEAK" not in read_text

            agent_request = agent_tools.execute_model_tool(
                "owner",
                agent_tools.DEVICE_PROPOSAL_TOOL_NAME,
                {
                    "device_key": "living-room-lamp",
                    "command": "off",
                    "arguments": {},
                },
                set(),
                owner=True,
            )
            agent_request_id = agent_request["result"]["request_id"]
            assert driver_calls[-1]["command"] == "set_brightness"
            assert client.post(
                f"/api/v1/control/action-requests/{agent_request_id}/deny"
            ).status_code == 200
            assert len(driver_calls) == 1

            # Paired apps need separate read/control permissions. Device control
            # is approval-only and cannot be upgraded to safe-automatic.
            pair = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "room-device-test",
                    "app_name": "Room Device Test",
                    "permissions": [
                        "agent.chat",
                        "devices.read",
                        "devices.control",
                        "tools.execute",
                    ],
                },
            ).json()
            assert client.post(
                "/api/v1/pairing/approve",
                json={"code": pair["code"]},
            ).status_code == 200
            headers = {"Authorization": f"Bearer {pair['claim_token']}"}
            identity = client.get("/api/v1/me", headers=headers).json()
            app_id = int(identity["id"])

            tool_listing = client.get("/api/v1/tools", headers=headers)
            assert tool_listing.status_code == 200
            by_key = {item["key"]: item for item in tool_listing.json()["items"]}
            assert by_key["devices.list"]["execution_policy"]["policy_mode"] == "read_only"
            assert by_key["devices.command"]["execution_policy"]["policy_mode"] == "approval_required"
            assert "safe_automatic" not in by_key["devices.command"]["execution_policy"]["allowed_modes"]

            unsafe_policy = client.put(
                f"/api/v1/control/action-policies/{app_id}/devices.command",
                json={"policy_mode": "safe_automatic"},
            )
            assert unsafe_policy.status_code == 422

            app_read = client.post(
                "/api/v1/tools/devices.list/execute",
                json={"arguments": {"room_key": "living-room", "limit": 20}},
                headers=headers,
            )
            assert app_read.status_code == 200, app_read.text
            assert any(
                row["device_key"] == "living-room-lamp"
                for row in app_read.json()["result"]["items"]
            )

            app_command = client.post(
                "/api/v1/tools/devices.command/execute",
                json={
                    "arguments": {
                        "device_key": "living-room-lamp",
                        "command": "off",
                        "arguments": {},
                    }
                },
                headers=headers,
            )
            assert app_command.status_code == 200, app_command.text
            assert app_command.json()["approval_required"] is True
            app_request_id = app_command.json()["result"]["request_id"]
            assert len(driver_calls) == 1

            app_visible = client.get(
                f"/api/v1/action-requests/{app_request_id}",
                headers=headers,
            )
            assert app_visible.status_code == 200
            app_visible_text = app_visible.text
            assert "arguments" not in app_visible.json()["request"]
            assert "PRIVATE_DEVICE_META" not in app_visible_text

            app_approved = client.post(
                f"/api/v1/control/action-requests/{app_request_id}/approve"
            )
            assert app_approved.status_code == 200
            assert len(driver_calls) == 2
            assert driver_calls[-1]["command"] == "off"

            # Ambient/cognitive systems may create suggestions, but the
            # suggestion itself has no execution authority.
            suggestion = room_device_automation.create_suggestion(
                source_kind="ambient",
                source_event_type="ambient.presence",
                device_key="living-room-lamp",
                command="on",
                arguments={},
                reason="The room is occupied and the lamp is off.",
            )
            assert suggestion["status"] == "suggested"
            assert len(driver_calls) == 2
            requested_suggestion = client.post(
                f"/api/v1/control/vp3-os/automation/suggestions/{suggestion['id']}/request"
            )
            assert requested_suggestion.status_code == 200
            suggestion_request_id = requested_suggestion.json()["result"]["request_id"]
            assert len(driver_calls) == 2
            refreshed_suggestion = room_device_automation.get_suggestion(suggestion["id"])
            assert refreshed_suggestion["status"] == "requested"
            assert refreshed_suggestion["action_request_id"] == suggestion_request_id
            assert client.post(
                f"/api/v1/control/action-requests/{suggestion_request_id}/approve"
            ).status_code == 200
            assert len(driver_calls) == 3
            assert driver_calls[-1]["command"] == "on"

            # Capability registry is intentionally aggregate-only; raw provider
            # and device metadata remain behind the devices.read tool surface.
            registry = client.get("/api/v1/capability-registry", headers=headers)
            assert registry.status_code == 200
            reg = registry.json()["vp3_os_room_device_automation"]
            assert reg["version"] == "v0.60"
            assert reg["rooms"] == 1
            assert reg["devices"] == 2
            assert reg["ambient_direct_execution"] is False
            registry_text = registry.text
            assert "PRIVATE_DEVICE_META" not in registry_text
            assert "MUST_NOT_LEAK" not in registry_text

            audit = client.get("/api/v1/control/activity?limit=500").text
            assert "automation.device.command.executed" in audit
            assert "PRIVATE_DEVICE_META" not in audit
            assert "MUST_NOT_LEAK" not in audit

            actions = client.get(
                "/api/v1/control/vp3-os/automation/actions?limit=100"
            ).json()["items"]
            assert sum(1 for row in actions if row["status"] == "completed") == 3
            assert any(row["action_request_id"] == request_id for row in actions)
    finally:
        room_device_automation.unregister_driver("test-local")
        os.environ.pop("VP3_OS_HARDWARE_ADAPTER", None)

print("VP3 OS v0.60 room-device automation regression passed")

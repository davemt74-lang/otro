from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v130-experience-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"

from app.database import db, initialize_database  # noqa: E402
from app.services import hardware_adapters, hardware_experience, vp3_os  # noqa: E402

initialize_database()

assert vp3_os.VP3_OS_VERSION == "v1.3"
normalized_dial = hardware_adapters.normalize_controller_message(
    {"type": "event", "seq": 7, "event": "control_dial", "action": "clockwise"}
)
assert normalized_dial["event"] == "control_dial"
assert normalized_dial["action"] == "clockwise"
try:
    hardware_adapters.normalize_controller_message(
        {"type": "event", "seq": 8, "event": "control_dial", "action": "spin"}
    )
except hardware_adapters.HardwareAdapterError:
    pass
else:
    raise AssertionError("Invalid control-dial action was accepted")
defaults = hardware_experience.get_settings()
assert defaults["enabled"] is True
assert defaults["brightness_percent"] == 70
assert defaults["volume_percent"] == 65
assert defaults["led_intensity_percent"] == 70
assert defaults["screen_timeout_seconds"] == 300
assert defaults["wake_behavior"] == "presence"
assert defaults["agent_button_action"] == "push_to_talk"
assert defaults["hold_action"] == "cancel"
assert defaults["display_detail"] == "standard"

updated = hardware_experience.update_settings(
    brightness_percent=82,
    volume_percent=55,
    led_intensity_percent=64,
    screen_timeout_seconds=180,
    wake_behavior="manual",
    agent_button_action="none",
    hold_action="meeting_toggle",
    display_detail="detailed",
    quiet_visuals=True,
)
assert updated["brightness_percent"] == 82
assert updated["volume_percent"] == 55
assert updated["hold_action"] == "meeting_toggle"
assert hardware_experience.button_policy() == {
    "agent_button_action": "none",
    "hold_action": "meeting_toggle",
}

EXPECTED = {
    "vp3_node": {
        "experience": "personal_voice",
        "required": {"agent_button", "status_light", "microphone", "speaker", "privacy_switch"},
        "display_cards": False,
    },
    "vp3_desk": {
        "experience": "desk_companion",
        "required": {"agent_button", "status_light", "microphone", "speaker", "privacy_switch", "display"},
        "display_cards": True,
    },
    "vp3_studio": {
        "experience": "creator_console",
        "required": {"agent_button", "status_light", "microphone", "speaker", "privacy_switch", "audio_io"},
        "display_cards": True,
    },
    "vp3_team_node": {
        "experience": "shared_room",
        "required": {"agent_button", "status_light", "privacy_switch"},
        "display_cards": True,
    },
    "vp3_pocket": {
        "experience": "portable_companion",
        "required": {"agent_button", "status_light", "microphone", "speaker", "privacy_switch", "display", "battery"},
        "display_cards": True,
    },
}


def ready_component(component: str) -> None:
    metadata = {}
    if component == "privacy_switch":
        metadata = {
            "engaged": False,
            "physical_disconnect": True,
            "microphone_powered": False,
        }
    elif component == "agent_button":
        metadata = {"pressed": False}
    vp3_os.report_hardware_state(
        component,
        present=True,
        ready=True,
        metadata=metadata,
    )


for profile_key, expected in EXPECTED.items():
    os.environ["VP3_OS_HARDWARE_PROFILE"] = profile_key
    vp3_os.clear_reported_hardware()
    experience = hardware_experience.profile_experience()
    assert experience["profile"]["key"] == profile_key
    assert experience["experience"] == expected["experience"]
    assert set(experience["required_hardware"]) == expected["required"]

    for component in experience["required_hardware"]:
        ready_component(component)

    state = hardware_experience.degraded_state()
    assert state["degraded"] is False, (profile_key, state)

    certification = hardware_experience.certification()
    assert certification["profile_key"] == profile_key
    assert certification["result"] == "passed", certification
    assert certification["required_failures"] == []

    required = experience["required_hardware"][0]
    vp3_os.report_hardware_state(required, present=False, ready=False)
    degraded = hardware_experience.degraded_state()
    assert degraded["degraded"] is True
    assert required in degraded["missing_hardware"]
    failed = hardware_experience.certification()
    assert failed["result"] == "failed"
    assert any(required in item for item in failed["required_failures"])
    ready_component(required)

    card = hardware_experience.upsert_card(
        f"product-{profile_key}",
        "system",
        f"{profile_key} system state",
        subtitle="Experience certification",
        priority=80,
        payload={"state": "ready"},
    )
    assert card["state"] == "active"
    cards = hardware_experience.list_cards(20)
    if expected["display_cards"]:
        assert any(item["card_key"] == card["card_key"] for item in cards)
    else:
        assert cards == []
    hardware_experience.set_card_state(card["card_key"], "dismissed")

os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_desk"
vp3_os.clear_reported_hardware()
for component in hardware_experience.profile_experience()["required_hardware"]:
    ready_component(component)
vp3_os.report_hardware_state(
    "presence_sensor",
    present=True,
    ready=True,
    metadata={"occupied": True},
)
hardware_experience.update_settings(wake_behavior="presence", agent_button_action="push_to_talk", hold_action="cancel")
hardware_experience.handle_hardware_event(
    {"event": "presence_sensor", "action": "present", "seq": 1}
)
status = hardware_experience.status()
assert status["runtime"]["screen_awake"] is True
hardware_experience.handle_hardware_event(
    {"event": "presence_sensor", "action": "absent", "seq": 2}
)
status = hardware_experience.status()
assert status["runtime"]["screen_awake"] is False
hardware_experience.handle_hardware_event(
    {"event": "agent_button", "action": "press", "seq": 3}
)
before_volume = hardware_experience.get_settings()["volume_percent"]
hardware_experience.handle_hardware_event(
    {"event": "control_dial", "action": "clockwise", "seq": 4}
)
assert hardware_experience.get_settings()["volume_percent"] == min(100, before_volume + 5)
hardware_experience.handle_hardware_event(
    {"event": "control_dial", "action": "counterclockwise", "seq": 5}
)
assert hardware_experience.get_settings()["volume_percent"] == before_volume
events = hardware_experience.recent_events(10)
assert any(
    item["event_type"] == "agent_button"
    and item["outcome"] == "delegated_to_physical_runtimes"
    for item in events
)
assert any(
    item["event_type"] == "control_dial"
    and item["outcome"] in {"volume_up", "volume_down"}
    for item in events
)

with db() as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM vp3_hardware_experience_certifications"
    ).fetchone()[0] >= 10
    assert connection.execute(
        "SELECT COUNT(*) FROM vp3_hardware_experience_events"
    ).fetchone()[0] >= 3

tmp.cleanup()
print("VP3 OS v1.3 hardware experience runtime passed")

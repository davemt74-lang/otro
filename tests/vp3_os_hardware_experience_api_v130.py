from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v130-api-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_desk"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.runtime import app  # noqa: E402
from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
from app.services import hardware_experience, vp3_os  # noqa: E402
from app.services.tasks import scheduler  # noqa: E402


def ready(component: str) -> None:
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


with TestClient(app) as client:
    scheduler.stop()

    assert client.get("/api/v1/control/vp3-os/hardware-experience").status_code == 401

    owner = client.post(
        "/__owner/session",
        headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
    )
    assert owner.status_code == 200

    for component in hardware_experience.profile_experience()["required_hardware"]:
        ready(component)

    status = client.get("/api/v1/control/vp3-os/hardware-experience")
    assert status.status_code == 200, status.text
    payload = status.json()
    assert payload["version"] == "v1.3"
    assert payload["vp3_os_version"] == "v1.3"
    assert payload["experience"]["profile"]["key"] == "vp3_desk"
    assert payload["experience"]["experience"] == "desk_companion"
    assert "display_cards" in payload["experience"]["capabilities"]
    assert payload["degraded"]["degraded"] is False

    settings = client.put(
        "/api/v1/control/vp3-os/hardware-experience/settings",
        json={
            "brightness_percent": 88,
            "volume_percent": 58,
            "led_intensity_percent": 62,
            "screen_timeout_seconds": 240,
            "wake_behavior": "presence",
            "agent_button_action": "push_to_talk",
            "hold_action": "meeting_toggle",
            "display_detail": "detailed",
            "quiet_visuals": False,
        },
    )
    assert settings.status_code == 200, settings.text
    updated = settings.json()["settings"]
    assert updated["brightness_percent"] == 88
    assert updated["volume_percent"] == 58
    assert updated["hold_action"] == "meeting_toggle"

    card = client.post(
        "/api/v1/control/vp3-os/hardware-experience/cards",
        json={
            "card_key": "api-agent-card",
            "card_type": "agent",
            "title": "Agent ready",
            "subtitle": "Desk experience",
            "priority": 90,
            "payload": {"state": "idle"},
        },
    )
    assert card.status_code == 200, card.text
    assert card.json()["card_key"] == "api-agent-card"

    refreshed = client.get("/api/v1/control/vp3-os/hardware-experience")
    assert refreshed.status_code == 200
    assert any(
        item["card_key"] == "api-agent-card"
        for item in refreshed.json()["cards"]
    )

    dismissed = client.put(
        "/api/v1/control/vp3-os/hardware-experience/cards/api-agent-card/state",
        json={"state": "dismissed"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["state"] == "dismissed"

    certification = client.post(
        "/api/v1/control/vp3-os/hardware-experience/certifications"
    )
    assert certification.status_code == 200, certification.text
    cert = certification.json()
    assert cert["result"] == "passed"
    assert cert["profile_key"] == "vp3_desk"

    certs = client.get(
        "/api/v1/control/vp3-os/hardware-experience/certifications?limit=5"
    )
    assert certs.status_code == 200
    assert certs.json()["items"][0]["profile_key"] == "vp3_desk"

    events = client.get(
        "/api/v1/control/vp3-os/hardware-experience/events?limit=5"
    )
    assert events.status_code == 200

    caps = client.get("/api/v1/capabilities")
    assert caps.status_code == 200
    capabilities = caps.json()
    assert capabilities["vp3_os"]["os_version"] == "v1.3"
    experience = capabilities["vp3_os_hardware_experience"]
    assert experience["version"] == "v1.3"
    assert experience["profile_key"] == "vp3_desk"
    assert experience["device_specific_profiles"] is True
    assert experience["normalized_event_bus"] is True
    assert experience["physical_action_authority"] is False
    for feature in (
        "vp3.os.v130",
        "vp3.os.hardware_experience.v1",
        "vp3.os.hardware_event_bus.v1",
        "vp3.os.display_cards.v1",
        "vp3.os.experience_certification.v1",
    ):
        assert feature in capabilities["features"], feature

tmp.cleanup()
print("VP3 OS v1.3 hardware experience API passed")

from __future__ import annotations

import os
import threading
from typing import Any

from . import providers
from .remote_identity import remote_identity_metadata

VP3_OS_PLATFORM_VERSION = "v0.10"
VP3_OS_VERSION = "v0.30"
VP3_OS_CONTRACT = "vp3-os-hardware-platform-v010-20260921"
PLACEMENT_MODES = ("LOCAL", "LOCAL_ONLY", "CLOUD", "HYBRID", "DEFER")

_PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "custom": {
        "label": "VP3 OS",
        "product": "Custom hardware",
        "portable": False,
        "shared": False,
        "creator": False,
        "expected_hardware": (),
    },
    "vp3_node": {
        "label": "VP3 Node",
        "product": "Personal AI appliance",
        "portable": False,
        "shared": False,
        "creator": False,
        "expected_hardware": ("agent_button", "status_light", "microphone", "speaker", "privacy_switch"),
    },
    "vp3_desk": {
        "label": "VP3 Desk",
        "product": "Desk AI companion",
        "portable": False,
        "shared": False,
        "creator": False,
        "expected_hardware": ("agent_button", "status_light", "microphone", "speaker", "privacy_switch", "display"),
    },
    "vp3_studio": {
        "label": "VP3 Studio",
        "product": "Creator AI appliance",
        "portable": False,
        "shared": False,
        "creator": True,
        "expected_hardware": ("agent_button", "status_light", "microphone", "speaker", "privacy_switch", "audio_io"),
    },
    "vp3_team_node": {
        "label": "VP3 Team Node",
        "product": "Shared AI appliance",
        "portable": False,
        "shared": True,
        "creator": False,
        "expected_hardware": ("agent_button", "status_light", "privacy_switch"),
    },
    "vp3_pocket": {
        "label": "VP3 Pocket",
        "product": "Portable AI companion",
        "portable": True,
        "shared": False,
        "creator": False,
        "expected_hardware": ("agent_button", "status_light", "microphone", "speaker", "privacy_switch", "display", "battery"),
    },
}

_HARDWARE_KEYS = (
    "agent_button",
    "status_light",
    "microphone",
    "speaker",
    "privacy_switch",
    "display",
    "audio_io",
    "battery",
    "camera",
    "storage",
    "accelerator",
)

_STATE_LOCK = threading.Lock()
_REPORTED_HARDWARE: dict[str, dict[str, Any]] = {}


def _text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[: max(1, limit)]


def hardware_keys() -> tuple[str, ...]:
    return _HARDWARE_KEYS


def configured_profile() -> str:
    requested = _text(os.environ.get("VP3_OS_HARDWARE_PROFILE") or "custom", 64).lower()
    return requested if requested in _PROFILE_DEFINITIONS else "custom"


def profile_definition(profile: str | None = None) -> dict[str, Any]:
    key = profile if profile in _PROFILE_DEFINITIONS else configured_profile()
    definition = _PROFILE_DEFINITIONS.get(str(key), _PROFILE_DEFINITIONS["custom"])
    return {
        "key": str(key) if key in _PROFILE_DEFINITIONS else "custom",
        "label": str(definition["label"]),
        "product": str(definition["product"]),
        "portable": bool(definition["portable"]),
        "shared": bool(definition["shared"]),
        "creator": bool(definition["creator"]),
        "expected_hardware": list(definition["expected_hardware"]),
    }


def report_hardware_state(component: str, *, present: bool, ready: bool, metadata: dict[str, Any] | None = None) -> None:
    """Driver-facing v0.10 reporting hook.

    v0.10 does not probe arbitrary host devices itself. Future hardware adapters
    report only normalized state through this function, which avoids claiming
    that a microphone, privacy switch, display, accelerator, or battery exists
    until an adapter has actually observed it.
    """
    key = _text(component, 64).lower()
    if key not in _HARDWARE_KEYS:
        raise ValueError("Unsupported VP3 OS hardware component")
    safe_metadata: dict[str, Any] = {}
    raw = metadata if isinstance(metadata, dict) else {}
    if key == "privacy_switch":
        safe_metadata["engaged"] = bool(raw.get("engaged"))
        safe_metadata["physical_disconnect"] = bool(raw.get("physical_disconnect"))
        powered = raw.get("microphone_powered")
        safe_metadata["microphone_powered"] = bool(powered) if powered is not None else None
    elif key == "agent_button":
        safe_metadata["pressed"] = bool(raw.get("pressed"))
    elif key == "storage":
        safe_metadata["bytes_total"] = max(0, int(raw.get("bytes_total") or 0))
        safe_metadata["bytes_free"] = max(0, int(raw.get("bytes_free") or 0))
    elif key == "accelerator":
        safe_metadata["kind"] = _text(raw.get("kind"), 80)

    with _STATE_LOCK:
        _REPORTED_HARDWARE[key] = {
            "present": bool(present),
            "ready": bool(ready) and bool(present),
            **safe_metadata,
        }


def clear_reported_hardware() -> None:
    """Test/driver lifecycle helper; does not alter persistent VP3 OS state."""
    with _STATE_LOCK:
        _REPORTED_HARDWARE.clear()


def hardware_inventory() -> dict[str, dict[str, Any]]:
    with _STATE_LOCK:
        reported = {key: dict(value) for key, value in _REPORTED_HARDWARE.items()}
    inventory: dict[str, dict[str, Any]] = {}
    for key in _HARDWARE_KEYS:
        item = reported.get(key, {})
        inventory[key] = {
            "present": bool(item.get("present")),
            "ready": bool(item.get("ready")),
        }
        if key == "privacy_switch":
            inventory[key]["engaged"] = bool(item.get("engaged"))
            inventory[key]["physical_disconnect"] = bool(item.get("physical_disconnect"))
            inventory[key]["microphone_powered"] = item.get("microphone_powered")
        elif key == "agent_button":
            inventory[key]["pressed"] = bool(item.get("pressed"))
        elif key == "storage":
            inventory[key]["bytes_total"] = max(0, int(item.get("bytes_total") or 0))
            inventory[key]["bytes_free"] = max(0, int(item.get("bytes_free") or 0))
        elif key == "accelerator":
            inventory[key]["kind"] = _text(item.get("kind"), 80)
    return inventory


def _compute_status() -> dict[str, Any]:
    try:
        raw = providers.inference_status()
    except Exception:
        raw = {}
    source = _text(raw.get("compute_source"), 80)
    return {
        "available": bool(raw.get("available")),
        "selected_provider": _text(raw.get("selected_provider"), 80),
        "model": _text(raw.get("model"), 160),
        "compute_source": source,
        "local_compute_ready": bool(raw.get("available")) and source == "homeserver_local",
        "external_provider_ready": bool(raw.get("available")) and source == "user_provider",
        "cloud_fallback_required": bool(raw.get("cloud_fallback_required")),
    }


def manifest(*, include_hardware: bool = True, include_device_id: bool = True) -> dict[str, Any]:
    profile = profile_definition()
    identity = remote_identity_metadata() if include_device_id else {}
    hardware = hardware_inventory()
    privacy_switch = hardware["privacy_switch"]
    result: dict[str, Any] = {
        "contract": VP3_OS_CONTRACT,
        "platform": "VP3 OS",
        "os_version": VP3_OS_VERSION,
        "platform_version": VP3_OS_PLATFORM_VERSION,
        "profile": profile,
        "device_id": _text(identity.get("device_id"), 100) if include_device_id else "",
        "hardware_revision": _text(os.environ.get("VP3_OS_HARDWARE_REVISION"), 64),
        "firmware_version": _text(os.environ.get("VP3_OS_FIRMWARE_VERSION"), 64),
        "placement_modes": list(PLACEMENT_MODES),
        "privacy": {
            "raw_audio_cloud_default": False,
            "microphone_requires_local_authorization": True,
            "privacy_switch_engaged": bool(privacy_switch.get("engaged")),
            "microphone_power_state_known": privacy_switch.get("microphone_powered") is not None,
            "physical_microphone_disconnect_reported": bool(
                privacy_switch.get("present") and privacy_switch.get("physical_disconnect")
            ),
            "physical_microphone_disconnect_verified": bool(
                privacy_switch.get("present")
                and privacy_switch.get("engaged")
                and privacy_switch.get("physical_disconnect")
                and privacy_switch.get("microphone_powered") is False
            ),
        },
    }
    if include_hardware:
        result["hardware"] = hardware
    return result


def capability_projection(*, include_device_id: bool = True) -> dict[str, Any]:
    """Sanitized platform metadata safe for capability discovery."""
    data = manifest(include_hardware=False, include_device_id=include_device_id)
    compute = _compute_status()
    result = {
        "contract": data["contract"],
        "platform": data["platform"],
        "os_version": data["os_version"],
        "platform_version": data["platform_version"],
        "profile": data["profile"],
        "hardware_revision": data["hardware_revision"],
        "firmware_version": data["firmware_version"],
        "placement_modes": data["placement_modes"],
        "privacy": data["privacy"],
        "compute": {
            "local_compute_ready": compute["local_compute_ready"],
            "external_provider_ready": compute["external_provider_ready"],
            "cloud_fallback_required": compute["cloud_fallback_required"],
        },
    }
    if include_device_id:
        result["device_id"] = data["device_id"]
    return result


def owner_status() -> dict[str, Any]:
    return {
        **manifest(include_hardware=True),
        "compute": _compute_status(),
        "authority": {
            "agent_brain": "vp3_os_existing",
            "cognition": "vp3_os_existing",
            "memory_and_knowledge": "vp3_os_existing",
            "voice": "vp3_os_existing",
            "cloud_bridge": "vp3_os_existing",
            "hardware_io": "vp3_os_hardware_adapters_v020",
            "physical_agent": "vp3_os_physical_agent_v030",
        },
    }


def _requested_hardware(work: dict[str, Any]) -> list[str]:
    raw = work.get("hardware")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw[:16]:
        key = _text(item, 64).lower()
        if key in _HARDWARE_KEYS and key not in result:
            result.append(key)
    return result


def placement_plan(
    work: dict[str, Any],
    *,
    cloud_ready: bool,
    cloud_allowed: bool,
) -> dict[str, Any]:
    """Resolve placement intent only; execution remains in existing runtimes."""
    sensitivity = _text(work.get("sensitivity") or "internal", 20).lower()
    if sensitivity not in {"public", "internal", "private", "secret"}:
        sensitivity = "internal"

    raw_audio = bool(work.get("raw_audio"))
    needs_local_data = bool(work.get("needs_local_data"))
    needs_cloud_model = bool(work.get("needs_cloud_model"))
    collaboration = bool(work.get("collaboration"))
    required_hardware = _requested_hardware(work)

    compute = _compute_status()
    local_compute_ready = bool(compute["local_compute_ready"])
    hardware = hardware_inventory()
    unavailable_hardware = [key for key in required_hardware if not hardware[key]["ready"]]
    requires_local_presence = bool(required_hardware or needs_local_data or raw_audio or sensitivity == "secret")

    placement = "DEFER"
    reason = "no_runtime_ready"

    if raw_audio or sensitivity == "secret":
        if unavailable_hardware:
            reason = "required_hardware_unavailable"
        else:
            placement = "LOCAL_ONLY"
            reason = "raw_audio_local_only" if raw_audio else "secret_local_only"
    elif unavailable_hardware:
        reason = "required_hardware_unavailable"
    elif needs_local_data:
        if needs_cloud_model and cloud_allowed and cloud_ready:
            placement = "HYBRID"
            reason = "local_data_cloud_reasoning"
        elif local_compute_ready:
            placement = "LOCAL"
            reason = "local_data_local_compute"
        else:
            reason = "local_compute_unavailable"
    elif required_hardware:
        if needs_cloud_model and cloud_allowed and cloud_ready:
            placement = "HYBRID"
            reason = "hardware_local_cloud_reasoning"
        else:
            placement = "LOCAL"
            reason = "hardware_local"
    elif collaboration and local_compute_ready and cloud_allowed and cloud_ready:
        placement = "HYBRID"
        reason = "local_compute_and_cloud_collaboration"
    elif needs_cloud_model:
        if cloud_allowed and cloud_ready:
            placement = "CLOUD"
            reason = "cloud_model_required"
        elif local_compute_ready:
            placement = "LOCAL"
            reason = "cloud_unavailable_local_compute"
        else:
            reason = "cloud_disallowed" if not cloud_allowed else "required_compute_unavailable"
    elif local_compute_ready:
        placement = "LOCAL"
        reason = "local_compute_preferred"
    elif cloud_allowed and cloud_ready:
        placement = "CLOUD"
        reason = "local_compute_unavailable_cloud_ready"
    else:
        # Non-inference OS work (status, local orchestration, device events) can
        # remain local even when no LLM provider is configured.
        placement = "LOCAL"
        reason = "local_os_runtime"

    return {
        "contract": VP3_OS_CONTRACT,
        "placement": placement,
        "reason": reason,
        "cloud_allowed": bool(cloud_allowed),
        "cloud_ready": bool(cloud_ready),
        "local_compute_ready": local_compute_ready,
        "requires_local_presence": requires_local_presence,
        "required_hardware": required_hardware,
        "unavailable_hardware": unavailable_hardware,
        "raw_audio_cloud_allowed": False,
    }

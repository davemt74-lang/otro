from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import vp3_os

HARDWARE_ADAPTER_VERSION = "v0.20"
HARDWARE_PROTOCOL = "vp3-hw-v1"
DEFAULT_BAUD = 115200
MAX_LINE_BYTES = 8192
MAX_EVENTS = 100
RECONNECT_SECONDS = 2.0
HANDSHAKE_TIMEOUT_SECONDS = 3.0

_CONTROLLER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_LIGHT_MODES = {
    "off",
    "idle",
    "listening",
    "thinking",
    "speaking",
    "privacy",
    "error",
    "updating",
}


@dataclass(frozen=True)
class AdapterConfig:
    mode: str
    port: str
    baud: int
    usb_vid: int | None
    usb_pid: int | None


class HardwareAdapterError(RuntimeError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_nonnegative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_hex_env(name: str) -> int | None:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return None
    try:
        return int(raw, 16) if raw.startswith("0x") else int(raw, 16)
    except ValueError:
        return None


def config() -> AdapterConfig:
    mode = str(os.environ.get("VP3_OS_HARDWARE_ADAPTER") or "disabled").strip().lower()
    if mode not in {"disabled", "serial"}:
        mode = "disabled"
    raw_baud = str(os.environ.get("VP3_OS_HARDWARE_BAUD") or DEFAULT_BAUD).strip()
    try:
        baud = int(raw_baud)
    except ValueError:
        baud = DEFAULT_BAUD
    if baud < 9600 or baud > 3_000_000:
        baud = DEFAULT_BAUD
    return AdapterConfig(
        mode=mode,
        port=_bounded_text(os.environ.get("VP3_OS_HARDWARE_PORT"), 260),
        baud=baud,
        usb_vid=_parse_hex_env("VP3_OS_HARDWARE_USB_VID"),
        usb_pid=_parse_hex_env("VP3_OS_HARDWARE_USB_PID"),
    )


def _safe_component_state(component: str, raw: Any) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    state: dict[str, Any] = {
        "present": bool(item.get("present")),
        "ready": bool(item.get("ready")),
    }
    if component == "agent_button":
        state["pressed"] = bool(item.get("pressed"))
    elif component == "privacy_switch":
        state["engaged"] = bool(item.get("engaged"))
        state["physical_disconnect"] = bool(item.get("physical_disconnect"))
        power = item.get("microphone_powered")
        state["microphone_powered"] = bool(power) if power is not None else None
    elif component == "storage":
        state["bytes_total"] = _safe_nonnegative_int(item.get("bytes_total"))
        state["bytes_free"] = _safe_nonnegative_int(item.get("bytes_free"))
    elif component == "accelerator":
        state["kind"] = _bounded_text(item.get("kind"), 80)
    elif component == "presence_sensor":
        state["occupied"] = bool(item.get("occupied"))
    return state


def normalize_controller_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise HardwareAdapterError("Hardware controller message must be an object.")
    kind = _bounded_text(message.get("type"), 40).lower()
    if kind == "hello":
        protocol = _bounded_text(message.get("protocol"), 40)
        controller_id = _bounded_text(message.get("controller_id"), 80)
        if protocol != HARDWARE_PROTOCOL:
            raise HardwareAdapterError("Hardware controller protocol is incompatible.")
        if not _CONTROLLER_ID.fullmatch(controller_id):
            raise HardwareAdapterError("Hardware controller identity is invalid.")
        raw_components = message.get("components")
        components = []
        if isinstance(raw_components, list):
            for item in raw_components[:32]:
                key = _bounded_text(item, 64).lower()
                if key in vp3_os.hardware_keys() and key not in components:
                    components.append(key)
        capabilities = []
        raw_capabilities = message.get("capabilities")
        if isinstance(raw_capabilities, list):
            for item in raw_capabilities[:64]:
                value = _bounded_text(item, 80)
                if value and value not in capabilities:
                    capabilities.append(value)
        return {
            "type": "hello",
            "protocol": protocol,
            "controller_id": controller_id,
            "firmware": _bounded_text(message.get("firmware"), 64),
            "hardware_revision": _bounded_text(message.get("hardware_revision"), 64),
            "components": components,
            "capabilities": capabilities,
        }

    if kind == "state":
        raw_components = message.get("components")
        if not isinstance(raw_components, dict):
            raise HardwareAdapterError("Hardware state message requires components.")
        components: dict[str, dict[str, Any]] = {}
        for key, value in list(raw_components.items())[:32]:
            component = _bounded_text(key, 64).lower()
            if component not in vp3_os.hardware_keys():
                continue
            components[component] = _safe_component_state(component, value)
        return {
            "type": "state",
            "seq": _safe_nonnegative_int(message.get("seq")),
            "components": components,
        }

    if kind == "event":
        event = _bounded_text(message.get("event"), 80).lower()
        action = _bounded_text(message.get("action"), 80).lower()
        if event not in {"agent_button", "privacy_switch", "presence_sensor", "wake_word", "voice_activity"}:
            raise HardwareAdapterError("Hardware event type is not allowlisted.")
        if event == "agent_button" and action not in {"press", "release", "hold"}:
            raise HardwareAdapterError("Agent button action is invalid.")
        if event == "privacy_switch" and action not in {"engaged", "disengaged"}:
            raise HardwareAdapterError("Privacy switch action is invalid.")
        if event == "presence_sensor" and action not in {"present", "absent"}:
            raise HardwareAdapterError("Presence sensor action is invalid.")
        if event == "wake_word" and action != "detected":
            raise HardwareAdapterError("Wake-word action is invalid.")
        if event == "voice_activity" and action not in {"started", "stopped"}:
            raise HardwareAdapterError("Voice-activity action is invalid.")
        return {
            "type": "event",
            "seq": _safe_nonnegative_int(message.get("seq")),
            "event": event,
            "action": action,
        }

    if kind == "ack":
        command_id = _bounded_text(message.get("command_id"), 80)
        return {
            "type": "ack",
            "command_id": command_id,
            "ok": bool(message.get("ok")),
            "error": _bounded_text(message.get("error"), 160),
        }

    raise HardwareAdapterError("Hardware controller message type is not supported.")


class HardwareAdapterManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._serial: Any = None
        self._controller: dict[str, Any] | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._event_handlers: list[Any] = []
        self._last_error = ""
        self._last_seen_at: str | None = None
        self._connected_at: str | None = None
        self._last_seq = 0
        self._last_state_seq = 0
        self._commands_sent = 0
        self._last_light_mode = "off"
        self._state = "stopped"

    def start(self) -> None:
        cfg = config()
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            if cfg.mode == "disabled":
                self._state = "disabled"
                self._controller = None
                self._connected_at = None
                self._last_error = ""
                vp3_os.clear_reported_hardware()
                return
            self._state = "starting"
            self._thread = threading.Thread(target=self._run_serial, name="vp3-hardware-adapter", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = None
        with self._lock:
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._close_serial()
        with self._lock:
            self._thread = None
            self._controller = None
            self._connected_at = None
            if config().mode == "disabled":
                self._state = "disabled"
            else:
                self._state = "stopped"
        vp3_os.clear_reported_hardware()

    def _serial_module(self):
        try:
            import serial  # type: ignore
            from serial.tools import list_ports  # type: ignore
        except Exception as exc:
            raise HardwareAdapterError("pyserial is unavailable in this VP3 OS build.") from exc
        return serial, list_ports

    def _select_port(self, list_ports) -> str:
        cfg = config()
        if cfg.port:
            return cfg.port
        for port in list_ports.comports():
            vid = getattr(port, "vid", None)
            pid = getattr(port, "pid", None)
            if cfg.usb_vid is not None and vid != cfg.usb_vid:
                continue
            if cfg.usb_pid is not None and pid != cfg.usb_pid:
                continue
            text = " ".join(
                str(value or "")
                for value in (
                    getattr(port, "description", ""),
                    getattr(port, "manufacturer", ""),
                    getattr(port, "product", ""),
                    getattr(port, "hwid", ""),
                )
            ).lower()
            if cfg.usb_vid is not None or cfg.usb_pid is not None or "vp3" in text:
                return str(getattr(port, "device", "") or "")
        return ""

    def _run_serial(self) -> None:
        while not self._stop.is_set():
            try:
                serial_module, list_ports = self._serial_module()
                port = self._select_port(list_ports)
                if not port:
                    with self._lock:
                        self._state = "waiting_for_controller"
                        self._last_error = ""
                    self._stop.wait(RECONNECT_SECONDS)
                    continue
                cfg = config()
                handle = serial_module.Serial(
                    port=port,
                    baudrate=cfg.baud,
                    timeout=0.5,
                    write_timeout=1.0,
                )
                with self._lock:
                    self._serial = handle
                    self._state = "handshaking"
                    self._last_error = ""
                self._send_wire({
                    "type": "hello",
                    "protocol": HARDWARE_PROTOCOL,
                    "client": "vp3-os",
                    "version": HARDWARE_ADAPTER_VERSION,
                })
                self._read_loop(handle)
            except HardwareAdapterError as exc:
                self._mark_disconnected(str(exc))
            except Exception as exc:
                self._mark_disconnected(f"{type(exc).__name__}: hardware controller unavailable")
            finally:
                self._close_serial()
            if not self._stop.is_set():
                self._stop.wait(RECONNECT_SECONDS)

    def _read_loop(self, handle: Any) -> None:
        handshake_started = time.monotonic()
        while not self._stop.is_set():
            line = handle.readline(MAX_LINE_BYTES + 1)
            if not line:
                with self._lock:
                    handshaken = self._controller is not None
                if not handshaken and (time.monotonic() - handshake_started) >= HANDSHAKE_TIMEOUT_SECONDS:
                    raise HardwareAdapterError("Hardware controller handshake timed out.")
                continue
            if len(line) > MAX_LINE_BYTES:
                raise HardwareAdapterError("Hardware controller message exceeded the size limit.")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HardwareAdapterError("Hardware controller sent invalid JSON.") from exc
            self.handle_message(message)

    def _close_serial(self) -> None:
        handle = None
        with self._lock:
            handle = self._serial
            self._serial = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def _mark_disconnected(self, error: str) -> None:
        with self._lock:
            self._state = "disconnected"
            self._controller = None
            self._last_error = _bounded_text(error, 240)
            self._connected_at = None
        vp3_os.clear_reported_hardware()

    def handle_message(self, message: Any) -> dict[str, Any]:
        item = normalize_controller_message(message)
        now = _iso_now()
        with self._lock:
            self._last_seen_at = now

        if item["type"] == "hello":
            # A controller reboot/re-handshake invalidates the prior device
            # snapshot until a fresh state frame arrives.
            vp3_os.clear_reported_hardware()
            with self._lock:
                self._controller = dict(item)
                self._connected_at = now
                self._state = "connected"
                self._last_error = ""
                self._last_seq = 0
                self._last_state_seq = 0
            return item

        with self._lock:
            handshaken = self._controller is not None
        if not handshaken:
            raise HardwareAdapterError("Hardware controller must complete the VP3 handshake first.")

        if item["type"] == "state":
            with self._lock:
                seq = int(item.get("seq") or 0)
                if seq and seq <= self._last_state_seq:
                    return {**item, "duplicate": True}
                if seq:
                    self._last_state_seq = seq
            self._apply_state(item)
            return {**item, "duplicate": False}

        if item["type"] == "event":
            with self._lock:
                seq = int(item.get("seq") or 0)
                if seq and seq <= self._last_seq:
                    return {**item, "duplicate": True}
                if seq:
                    self._last_seq = seq
                event = {**item, "received_at": now}
                self._events.append(event)
                handlers = list(self._event_handlers)
            for handler in handlers:
                try:
                    handler(dict(event))
                except Exception:
                    # Hardware input must remain available even if an optional
                    # higher-level consumer fails. Consumers own their errors.
                    pass
            return {**item, "duplicate": False}

        return item

    def _apply_state(self, message: dict[str, Any]) -> None:
        components = message.get("components") or {}
        privacy = components.get("privacy_switch") if isinstance(components, dict) else None
        mic = components.get("microphone") if isinstance(components, dict) else None

        with self._lock:
            controller = dict(self._controller) if self._controller else {}
        capabilities = {
            str(value)
            for value in controller.get("capabilities", [])
            if isinstance(value, str)
        }
        privacy_engaged = bool(privacy.get("engaged")) if isinstance(privacy, dict) else False
        physical_disconnect = bool(
            isinstance(privacy, dict)
            and privacy.get("physical_disconnect")
            and "mic_power_cut" in capabilities
        )
        power_sense_supported = "mic_power_sense" in capabilities
        microphone_powered = (
            privacy.get("microphone_powered")
            if isinstance(privacy, dict) and power_sense_supported
            else None
        )
        privacy_fault = bool(
            isinstance(privacy, dict)
            and privacy_engaged
            and (
                not physical_disconnect
                or not power_sense_supported
                or microphone_powered is not False
            )
        )

        declared_components = {
            str(value)
            for value in controller.get("components", [])
            if isinstance(value, str) and str(value) in vp3_os.hardware_keys()
        }
        for missing in sorted(declared_components.difference(components.keys())):
            vp3_os.report_hardware_state(missing, present=False, ready=False)

        for component, state in components.items():
            metadata: dict[str, Any] = {}
            ready = bool(state.get("ready"))
            if component == "privacy_switch":
                metadata = {
                    "engaged": privacy_engaged,
                    "physical_disconnect": physical_disconnect,
                    "microphone_powered": microphone_powered,
                }
                if privacy_fault:
                    ready = False
            elif component == "storage":
                metadata = {
                    "bytes_total": state.get("bytes_total", 0),
                    "bytes_free": state.get("bytes_free", 0),
                }
            elif component == "accelerator":
                metadata = {"kind": state.get("kind", "")}
            elif component == "presence_sensor":
                metadata = {"occupied": bool(state.get("occupied"))}
            elif component == "agent_button":
                metadata = {"pressed": bool(state.get("pressed"))}
            vp3_os.report_hardware_state(
                component,
                present=bool(state.get("present")),
                ready=ready,
                metadata=metadata,
            )

        if isinstance(mic, dict):
            mic_ready = bool(mic.get("ready"))
            # A physically engaged privacy switch makes microphone capture
            # unavailable regardless of what the USB audio device reports.
            if privacy_engaged:
                mic_ready = False
            vp3_os.report_hardware_state(
                "microphone",
                present=bool(mic.get("present")),
                ready=mic_ready,
            )

        if privacy_fault:
            with self._lock:
                self._last_error = "Physical microphone disconnect verification failed."
                self._state = "hardware_fault"
        else:
            with self._lock:
                if self._controller is not None:
                    self._state = "connected"
                    self._last_error = ""

    def _send_wire(self, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_LINE_BYTES:
            raise HardwareAdapterError("Hardware controller command exceeded the size limit.")
        with self._lock:
            handle = self._serial
        if handle is None:
            raise HardwareAdapterError("Hardware controller is not connected.")
        handle.write(encoded)
        handle.flush()

    def set_status_light(self, mode: str) -> dict[str, Any]:
        normalized = _bounded_text(mode, 32).lower()
        if normalized not in _LIGHT_MODES:
            raise HardwareAdapterError("Status light mode is invalid.")
        with self._lock:
            controller = dict(self._controller) if self._controller else None
        if not controller:
            raise HardwareAdapterError("Hardware controller is not connected.")
        if "status_light" not in set(controller.get("capabilities", [])):
            raise HardwareAdapterError("Hardware controller does not support status light control.")
        command_id = f"light-{int(time.time() * 1000)}"
        self._send_wire({
            "type": "command",
            "command_id": command_id,
            "command": "status_light.set",
            "args": {"mode": normalized},
        })
        with self._lock:
            self._commands_sent += 1
            self._last_light_mode = normalized
        return {"accepted": True, "command_id": command_id, "mode": normalized}

    def add_event_handler(self, handler: Any) -> None:
        if not callable(handler):
            raise HardwareAdapterError("Hardware event handler must be callable.")
        with self._lock:
            if handler not in self._event_handlers:
                self._event_handlers.append(handler)

    def remove_event_handler(self, handler: Any) -> None:
        with self._lock:
            self._event_handlers = [item for item in self._event_handlers if item != handler]

    def events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), MAX_EVENTS))
        with self._lock:
            return [dict(item) for item in list(self._events)[-bounded:]]

    def status(self) -> dict[str, Any]:
        cfg = config()
        with self._lock:
            controller = dict(self._controller) if self._controller else None
            return {
                "version": HARDWARE_ADAPTER_VERSION,
                "protocol": HARDWARE_PROTOCOL,
                "mode": cfg.mode,
                "state": self._state,
                "connected": self._state in {"connected", "hardware_fault"},
                "controller": controller,
                "last_seen_at": self._last_seen_at,
                "connected_at": self._connected_at,
                "last_error": self._last_error,
                "events_buffered": len(self._events),
                "commands_sent": self._commands_sent,
                "last_light_mode": self._last_light_mode,
                "configured_port": cfg.port or None,
                "configured_vid": cfg.usb_vid,
                "configured_pid": cfg.usb_pid,
            }


def public_capability() -> dict[str, Any]:
    return {
        "version": HARDWARE_ADAPTER_VERSION,
        "protocol": HARDWARE_PROTOCOL,
        "supported": True,
        "owner_managed": True,
    }


def paired_status() -> dict[str, Any]:
    raw = manager.status()
    controller = raw.get("controller") if isinstance(raw.get("controller"), dict) else None
    safe_controller = None
    if controller:
        safe_controller = {
            "firmware": _bounded_text(controller.get("firmware"), 64),
            "hardware_revision": _bounded_text(controller.get("hardware_revision"), 64),
            "components": [
                _bounded_text(item, 64)
                for item in controller.get("components", [])
                if _bounded_text(item, 64) in vp3_os.hardware_keys()
            ][:32],
            "capabilities": [
                _bounded_text(item, 80)
                for item in controller.get("capabilities", [])
                if _bounded_text(item, 80)
            ][:64],
        }
    privacy = vp3_os.manifest(include_hardware=False, include_device_id=False)["privacy"]
    return {
        "version": HARDWARE_ADAPTER_VERSION,
        "protocol": HARDWARE_PROTOCOL,
        "connected": bool(raw.get("connected")),
        "state": _bounded_text(raw.get("state"), 40),
        "controller": safe_controller,
        "privacy": {
            "privacy_switch_engaged": bool(privacy.get("privacy_switch_engaged")),
            "physical_microphone_disconnect_reported": bool(
                privacy.get("physical_microphone_disconnect_reported")
            ),
            "physical_microphone_disconnect_verified": bool(
                privacy.get("physical_microphone_disconnect_verified")
            ),
        },
    }


manager = HardwareAdapterManager()


def start() -> None:
    manager.start()


def stop() -> None:
    manager.stop()


def status() -> dict[str, Any]:
    return manager.status()


def set_status_light(mode: str) -> dict[str, Any]:
    return manager.set_status_light(mode)


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    return manager.events(limit=limit)


def add_event_handler(handler: Any) -> None:
    manager.add_event_handler(handler)


def remove_event_handler(handler: Any) -> None:
    manager.remove_event_handler(handler)

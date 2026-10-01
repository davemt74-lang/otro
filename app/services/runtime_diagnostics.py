"""Owner-only read-only installed HomeServer capability inventory and opt-in safe probes.

Inventory is evidence from THIS running process and machine, not a claim that
features present in source repositories have passed end-to-end validation.
Never captures audio, opens cameras, sends provider secrets or initiates repairs.
"""
from __future__ import annotations
from datetime import datetime, timezone
import os
import platform
import shutil
from typing import Any, Callable

from . import (
    device_audio, homeserver_media_tools, local_voice, physical_meeting,
    providers, tracky_physical_context, vp3_os,
)

CONTRACT = "vp3.homeserver.runtime-diagnostics.v1"
KINDS = ("operational", "degraded", "missing", "unsupported", "not_verified")


def _entry(name: str, status: str, evidence: str, next_step: str, **safe: Any) -> dict[str, Any]:
    return {
        "name": name, "status": status, "evidence": evidence,
        "next_step": next_step, **safe,
    }


def _observe(name: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        state = callback()
        if not isinstance(state, dict):
            return {"error": "UnexpectedDiagnosticResult"}
        return state
    except Exception as exc:
        # Never leak exception messages: filesystem paths and credentials
        # must not escape diagnostics into Cloud / Agent Brain.
        return {"error": type(exc).__name__}


def inventory(*, probe: bool = False) -> dict[str, Any]:
    voice = _observe("voice", local_voice.status)
    audio = _observe("audio", device_audio.status)
    media = _observe("media", homeserver_media_tools.public_capability)
    inference = _observe("inference", providers.inference_status)
    meeting = _observe("meeting", physical_meeting.public_capability)
    hardware = _observe("hardware", vp3_os.hardware_inventory)
    tracky = _observe("tracky", tracky_physical_context.sync_status)

    checks: list[dict[str, Any]] = []
    stt = voice.get("stt") if isinstance(voice.get("stt"), dict) else {}
    tts = voice.get("tts") if isinstance(voice.get("tts"), dict) else {}
    stt_ready = bool(stt.get("available"))
    tts_ready = bool(tts.get("available"))
    checks.append(_entry(
        "transcription", "not_verified" if stt_ready else "missing",
        "Managed whisper.cpp model and executable found." if stt_ready
        else "Managed speech recognition executable or model not ready.",
        "Run an owner-approved sample transcription on the installed device."
        if stt_ready else "Install or repair the managed Whisper package and model.",
        installed=stt_ready, model=str(stt.get("model") or "")[:80],
        live_audio_tested=False,
    ))
    checks.append(_entry(
        "voice_output", "not_verified" if tts_ready else "missing",
        "Managed Piper runtime and selected voice available." if tts_ready
        else "Managed Piper runtime or selected voice unavailable.",
        "Perform owner-initiated speech synthesis and speaker playback."
        if tts_ready else "Install or repair the managed Piper package and voice.",
        installed=tts_ready, voice=str(tts.get("voice") or "")[:80],
        playback_tested=False,
    ))
    audio_ready = bool(audio.get("available"))
    mic = audio_ready and audio.get("default_input_index") is not None
    speaker = audio_ready and audio.get("default_output_index") is not None
    checks.append(_entry(
        "microphone_capture", "not_verified" if mic else "missing",
        "PortAudio exposes a default microphone." if mic else
        "PortAudio or a default microphone has not been detected.",
        "Request explicit owner consent for a short microphone recording."
        if mic else "Install PortAudio/sounddevice and configure an input device.",
        backend="sounddevice-portaudio", device_detected=bool(mic),
        recording_tested=False,
    ))
    checks.append(_entry(
        "speaker_device", "not_verified" if speaker else "missing",
        "PortAudio exposes a default output device." if speaker else
        "A default audio output device has not been detected.",
        "Play a short owner-initiated test audio clip." if speaker else
        "Configure a speaker/audio output and its drivers.",
        device_detected=bool(speaker), playback_tested=False,
    ))
    ffmpeg = bool(media.get("healthy") and media.get("ffmpeg_available") and media.get("ffprobe_available"))
    checks.append(_entry(
        "video_processing", "not_verified" if ffmpeg else "missing",
        "Packaged FFmpeg and FFprobe health checks passed." if ffmpeg else
        "Managed FFmpeg/FFprobe is absent or failed integrity/version checks.",
        "Run an owner-initiated sample transcode and verify the derivative."
        if ffmpeg else "Install or repair the managed FFmpeg/FFprobe package.",
        installed=ffmpeg, hashes_verified=media.get("hashes_verified"),
        version=str(media.get("ffmpeg_version") or "")[:80], transcode_tested=False,
    ))
    checks.append(_entry(
        "recording_retention", "unsupported",
        "Physical meeting transcription currently does not persist raw audio; "
        "a saved camera recording workflow has not been certified.",
        "Define governed saved audio/video capture, consent, retention and replay acceptance.",
        meeting_transcription_supported=bool(meeting.get("local_streaming_stt")),
        raw_audio_persisted=bool(meeting.get("raw_audio_persisted")),
        saved_video_recording_tested=False,
    ))
    camera = hardware.get("camera") if isinstance(hardware.get("camera"), dict) else {}
    camera_ready = bool(camera.get("present") and camera.get("ready"))
    checks.append(_entry(
        "camera_hardware", "not_verified" if camera_ready else "missing",
        "Camera is enumerated and hardware reports ready." if camera_ready else
        "No ready camera is reported in the HomeServer hardware inventory.",
        "With camera owner consent, capture a frame and verify device release."
        if camera_ready else "Connect a supported camera and configure its device driver.",
        enumerated=bool(camera.get("present")), ready=camera_ready, frames_tested=False,
    ))
    # Do not call public_capability here: it may recover stale requests and
    # would make an ostensibly read-only diagnosis mutate Tracky state.
    tracky_provider, tracky_capabilities, provider_name = tracky_physical_context._provider_snapshot()
    eyes_ready = bool(camera_ready and tracky_provider is not None)
    checks.append(_entry(
        "agent_eyes", "not_verified" if eyes_ready else "missing",
        "Camera and Tracky perception provider are registered." if eyes_ready else
        "Camera and/or Tracky perception provider is not ready on this runtime.",
        "Run consent-governed camera-to-detector-to-Agent-Brain acceptance."
        if eyes_ready else "Install/register camera and Tracky perception runtime before testing Agent Eyes.",
        provider_registered=bool(tracky_provider),
        camera_ready=camera_ready,
        semantic_sync_failures=int(tracky.get("consecutive_failures") or 0),
        frame_inference_tested=False,
    ))
    local = next((row for row in inference.get("providers", [])
        if isinstance(row, dict) and row.get("provider_key") == "ollama"), {})
    ollama_selected = bool(local.get("enabled")) and bool(local.get("model"))
    ollama_live: dict[str, Any] = {"attempted": False}
    if probe and ollama_selected:
        try:
            discovered = providers.discover_ollama_models()
            ollama_live = {
                "attempted": True,
                "reachable": bool(discovered.get("reachable")),
                "model_installed": str(local.get("model")) in (discovered.get("models") or []),
            }
        except Exception:
            ollama_live = {"attempted": True, "reachable": False, "model_installed": False}
    local_checked = bool(ollama_live.get("attempted"))
    installed_model = bool(ollama_live.get("model_installed"))
    model_status = (
        "degraded" if local_checked and not installed_model else
        "not_verified" if ollama_selected else "missing"
    )
    checks.append(_entry(
        "local_llm", model_status,
        "Ollama responds and configured model is listed; generation not yet measured."
        if installed_model else "Ollama is configured but its installed model has not been verified."
        if ollama_selected and not local_checked else
        "No reachable configured Ollama model." if local_checked else
        "No enabled Ollama model configured.",
        "Perform an owner-initiated generation/tool-call benchmark."
        if installed_model else "Run safe Ollama connectivity probe and install the selected model."
        if ollama_selected else "Install Ollama and configure a local model in Agent Brain.",
        configured=ollama_selected, connectivity_tested=local_checked,
        model_installed=installed_model if local_checked else None,
        generation_tested=False,
    ))
    user_providers = [
        {"provider": str(p.get("provider_key") or "")[:40], "configured": bool(p.get("credential_configured")),
         "ready": bool(p.get("ready"))}
        for p in (inference.get("providers") or []) if isinstance(p, dict)
        and p.get("provider_key") not in ("ollama",)
    ]
    checks.append(_entry(
        "provider_routing", "not_verified" if any(p["ready"] for p in user_providers) else "missing",
        "HomeServer local user API-key status only; keys are never returned.",
        "Verify model response locally and the Cloud system-funded entitlement/token gate independently.",
        homeserver_provider_status=user_providers,
        cloud_system_gate_tested=False,
        cloud_account_byok_tested=False,
    ))
    checks.append(_entry(
        "hardware_acceleration", "not_verified",
        "CPU architecture and NVIDIA management utility detection only; GPU processing not benchmarked.",
        "Run bounded CPU/GPU inference and FFmpeg hardware-codec benchmarks with owner consent.",
        cpu_count=os.cpu_count() or 0,
        architecture=platform.machine()[:40],
        nvidia_smi_detected=bool(shutil.which("nvidia-smi")),
        gpu_tested=False,
    ))
    counts = {kind: sum(c["status"] == kind for c in checks) for kind in KINDS}
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "installed_homeserver",
        "read_only": True,
        "safe_connectivity_probe": bool(probe),
        "recorded_media": False,
        "requested_repairs": False,
        "raw_secrets_included": False,
        "summary": counts,
        "checks": checks,
        "certified_end_to_end": False,
    }

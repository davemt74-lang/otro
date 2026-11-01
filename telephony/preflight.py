"""PH3H non-billable HomeServer phone worker preflight.

This checks only local configuration. No Twilio/LiveKit API call, audio
recording or HomeServer Chat turn is made, and no credential is printed.
"""

from __future__ import annotations

import json
import os
from typing import Mapping
from urllib.parse import urlparse

from .livekit_voice import WorkerSettings
from .voice_turns import VoiceTurnError


STAGING_TESTS = (
    "LiveKit dispatch, inbound SIP trunk and headers_to_attributes",
    "Twilio carrier child CallSid, signed parent proof and Cloud resolve",
    "HomeServer separate secondary Agent grant and per-turn revocation",
    "Live caller disclosure, speech transcription and synthesized reply",
    "Barge-in, silence, failed SIP and voicemail fallback",
)


def inspect_worker_config(settings: WorkerSettings, environment: Mapping[str, str]) -> dict:
    """Small stable report that cannot accidentally include secret values."""
    checks: list[dict[str, str]] = []

    def add(code: str, label: str, valid: bool) -> None:
        checks.append({"code": code, "label": label, "status": "pass" if valid else "fail"})

    add("mode", "Cloud-owned Twilio number uses signed Cloud resolve backend",
        settings.resolve_backend == "cloud")
    add("cloud_origin", "Fixed HTTPS Cloud origin and trusted local Chat callback",
        settings.edge_url.startswith("https://") and settings.callback_url.endswith("/api/v1/phone/agent"))
    add("worker_secret", "Separate Cloud SIP signing secret is at least 32 characters",
        len(settings.worker_secret) >= 32 and settings.worker_secret != settings.callback_secret)
    try:
        settings.validate()
        configuration_valid = True
    except (VoiceTurnError, ValueError):
        configuration_valid = False
    add("worker_settings", "Twilio account, SIP trunk, STT/TTS and Chat settings validate",
        configuration_valid)
    livekit_url = urlparse(environment.get("LIVEKIT_URL", ""))
    add("livekit_url", "LiveKit URL is a fixed secure WebSocket endpoint",
        livekit_url.scheme == "wss" and bool(livekit_url.hostname)
        and not any((livekit_url.username, livekit_url.password, livekit_url.query,
                     livekit_url.fragment, livekit_url.path not in ("", "/"))))
    add("livekit_credentials", "LiveKit worker API key and secret are set",
        bool(environment.get("LIVEKIT_API_KEY")) and bool(environment.get("LIVEKIT_API_SECRET")))
    add("callback_secret", "Dedicated HomeServer Chat signing secret is set",
        bool(settings.callback_secret) and settings.callback_secret != settings.worker_secret)
    return {"section": "PH3H", "checks": checks,
            "ready_for_staging": all(item["status"] == "pass" for item in checks),
            "live_certified": False, "live_tests_pending": list(STAGING_TESTS)}


def main() -> None:
    report = inspect_worker_config(WorkerSettings.from_environment(), os.environ)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ready_for_staging"] else 1)


if __name__ == "__main__":
    main()

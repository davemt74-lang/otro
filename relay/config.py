from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _origins() -> tuple[str, ...]:
    raw = os.environ.get(
        "HOMESERVER_RELAY_ALLOWED_ORIGINS",
        "https://vp3.me,https://www.vp3.me",
    )
    values = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    return values or ("https://vp3.me", "https://www.vp3.me")


@dataclass(frozen=True)
class RelaySettings:
    data_dir: Path
    database_path: Path
    allowed_origins: tuple[str, ...]
    claim_ttl_seconds: int
    claim_attempt_limit: int
    claim_attempt_window_seconds: int
    request_timeout_seconds: int
    max_message_bytes: int
    max_devices: int
    unclaimed_device_ttl_hours: int
    event_retention_days: int


def load_settings() -> RelaySettings:
    data_dir = Path(
        os.environ.get("HOMESERVER_RELAY_DATA_DIR") or "./relay-data"
    ).expanduser().resolve()
    return RelaySettings(
        data_dir=data_dir,
        database_path=data_dir / "relay.db",
        allowed_origins=_origins(),
        claim_ttl_seconds=_bounded_int("HOMESERVER_RELAY_CLAIM_TTL", 600, 120, 3600),
        claim_attempt_limit=_bounded_int("HOMESERVER_RELAY_CLAIM_ATTEMPTS", 10, 3, 100),
        claim_attempt_window_seconds=_bounded_int(
            "HOMESERVER_RELAY_CLAIM_WINDOW", 600, 60, 3600
        ),
        request_timeout_seconds=_bounded_int(
            "HOMESERVER_RELAY_REQUEST_TIMEOUT", 135, 10, 180
        ),
        max_message_bytes=_bounded_int(
            "HOMESERVER_RELAY_MAX_MESSAGE_BYTES", 262_144, 16_384, 1_048_576
        ),
        max_devices=_bounded_int("HOMESERVER_RELAY_MAX_DEVICES", 10_000, 1, 1_000_000),
        unclaimed_device_ttl_hours=_bounded_int(
            "HOMESERVER_RELAY_UNCLAIMED_TTL_HOURS", 24, 1, 168
        ),
        event_retention_days=_bounded_int(
            "HOMESERVER_RELAY_EVENT_RETENTION_DAYS", 30, 1, 365
        ),
    )


settings = load_settings()

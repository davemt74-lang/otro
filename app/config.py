from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _allowed_origins() -> tuple[str, ...]:
    raw = os.environ.get(
        "HOMESERVER_ALLOWED_ORIGINS",
        "https://vp3.me,https://www.vp3.me",
    )
    origins: list[str] = []
    for value in raw.split(","):
        origin = value.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _default_data_dir() -> Path:
    explicit = str(os.environ.get("HOMESERVER_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()

    legacy = Path.home() / ".homeserver"
    if os.name != "nt":
        return legacy

    local_app_data = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    preferred = local_app_data / "HomeServer" / "Data"
    if preferred.exists():
        return preferred
    if legacy.exists():
        # Direct module users remain backward compatible. The Windows launcher
        # performs the one-time legacy -> LocalAppData move before importing us.
        return legacy
    return preferred


@dataclass(frozen=True)
class Settings:
    app_name: str = "HomeServer"
    version: str = "0.15.0"
    host: str = "127.0.0.1"
    port: int = 4377
    data_dir: Path = field(default_factory=_default_data_dir)
    max_upload_bytes: int = 10 * 1024 * 1024
    max_backup_upload_bytes: int = 512 * 1024 * 1024
    max_backup_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    max_backup_entries: int = 10000
    max_remote_bridge_message_bytes: int = 256 * 1024
    allowed_origins: tuple[str, ...] = field(default_factory=_allowed_origins)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "homeserver.db"

    @property
    def knowledge_files_dir(self) -> Path:
        return self.data_dir / "knowledge" / "files"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def restore_dir(self) -> Path:
        return self.data_dir / "restore"

    @property
    def pending_restore_dir(self) -> Path:
        return self.restore_dir / "pending"

    @property
    def runtime_dir(self) -> Path:
        return self.data_dir / "runtime"

    @property
    def owner_secret_path(self) -> Path:
        return self.data_dir / "security" / "owner-bootstrap.dat"

    @property
    def remote_bridge_secret_path(self) -> Path:
        return self.data_dir / "security" / "remote-bridge.dat"

    @property
    def provider_credentials_path(self) -> Path:
        return self.data_dir / "security" / "provider-credentials.dat"

    @property
    def bootstrap_state_path(self) -> Path:
        return self.runtime_dir / "bootstrap-state.json"


settings = Settings()
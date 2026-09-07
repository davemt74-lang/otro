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


@dataclass(frozen=True)
class Settings:
    app_name: str = "HomeServer"
    version: str = "0.6.0"
    host: str = "127.0.0.1"
    port: int = 4377
    data_dir: Path = Path(os.environ.get("HOMESERVER_DATA_DIR", Path.home() / ".homeserver"))
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_origins: tuple[str, ...] = field(default_factory=_allowed_origins)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "homeserver.db"

    @property
    def knowledge_files_dir(self) -> Path:
        return self.data_dir / "knowledge" / "files"


settings = Settings()

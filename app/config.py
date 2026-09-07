from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "HomeServer"
    version: str = "0.3.0"
    host: str = "127.0.0.1"
    port: int = 4377
    data_dir: Path = Path(os.environ.get("HOMESERVER_DATA_DIR", Path.home() / ".homeserver"))
    max_upload_bytes: int = 10 * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.data_dir / "homeserver.db"

    @property
    def knowledge_files_dir(self) -> Path:
        return self.data_dir / "knowledge" / "files"


settings = Settings()

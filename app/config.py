from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "HomeServer"
    host: str = "127.0.0.1"
    port: int = 4377
    data_dir: Path = Path(os.environ.get("HOMESERVER_DATA_DIR", Path.home() / ".homeserver"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "homeserver.db"


settings = Settings()

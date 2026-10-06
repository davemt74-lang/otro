"""Stable running-build identity without disclosing local paths or credentials."""
from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def runtime_build_id() -> str:
    digest = hashlib.sha256()
    if getattr(sys, "frozen", False):
        with Path(sys.executable).open("rb") as executable:
            for block in iter(lambda: executable.read(1024 * 1024), b""):
                digest.update(block)
    else:
        root = Path(__file__).resolve().parents[2]
        for directory in ("app", "desktop", "ui", "database"):
            for path in sorted((root / directory).rglob("*")):
                if path.is_file() and path.suffix in {".py", ".js", ".css", ".html", ".sql"}:
                    digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(path.read_bytes())
    return digest.hexdigest()

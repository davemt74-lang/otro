from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.single_instance import SingleInstance  # noqa: E402


with tempfile.TemporaryDirectory(prefix="homeserver-single-instance-") as data_dir:
    first = SingleInstance(data_dir)
    second = SingleInstance(data_dir)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()

    third = SingleInstance(data_dir)
    assert third.acquire() is True
    third.release()

print("HomeServer single-instance test passed")

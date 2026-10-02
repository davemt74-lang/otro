"""Strict offline integrity check for the packaged native Tracky Haar detector.

The expected digest is pinned to the reviewed OpenCV frontalface cascade.
This verifies file integrity, not an installer signature or physical hardware.
Never downloads models, enumerates cameras or opens a camera during inspection.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

MODEL_FILE = "haarcascade_frontalface_default.xml"
EXPECTED_HAAR_SHA256 = "0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0"
MAX_MODEL_BYTES = 2_000_000
MIN_MODEL_BYTES = 100_000


def verify_file(path: Path) -> bool:
    """No writable hash manifest: compare against the reviewed code constant."""
    try:
        if not path.is_file() or not MIN_MODEL_BYTES <= path.stat().st_size <= MAX_MODEL_BYTES:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(131072), b""):
                digest.update(chunk)
        return digest.hexdigest() == EXPECTED_HAAR_SHA256
    except (OSError, ValueError):
        return False


def inspect_module(cv2: Any) -> dict[str, Any]:
    """Read-only module asset inspection; never expose local filesystem paths."""
    version = str(getattr(cv2, "__version__", "unknown"))[:64]
    try:
        path = Path(cv2.data.haarcascades) / MODEL_FILE
        present = path.is_file()
        verified = present and verify_file(path)
    except (OSError, AttributeError, TypeError, ValueError):
        present, verified = False, False
    return {
        "runtime_version": version,
        "model_present": present,
        "model_integrity_verified": verified,
        "model_origin": "packaged_opencv",
        "model_load_source": "local_filesystem_only",
        "network_fetch": False,
        "integrity_state": ("verified" if verified else "model_missing" if not present
                            else "model_digest_mismatch"),
        "hardware_certified": False,
    }

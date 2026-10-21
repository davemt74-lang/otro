"""Measure the exact staged Windows payload and PyInstaller extraction budget."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

MIB = 1024 * 1024
MARGIN = 64 * MIB


def measure(root: Path) -> dict:
    from PyInstaller.archive.readers import CArchiveReader

    root = root.resolve()
    files = [root / "HomeServer.exe"]
    required = [root / "tools" / "browser" / "manifest.json"] + [
        root / "tools" / "ffmpeg" / name for name in ("manifest.json", "ffmpeg.exe", "ffprobe.exe")]
    if any(not p.is_file() for p in required):
        raise ValueError("Staged runtime files are incomplete")
    for component in ("browser", "ffmpeg"):
        directory = root / "tools" / component
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"Missing staged {component} runtime")
        if any(p.is_symlink() for p in directory.rglob("*")):
            raise ValueError("Payload contains a symbolic directory or file")
        files.extend(p for p in directory.rglob("*") if p.is_file())
    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in files):
        raise ValueError("Payload contains an external or symbolic file")
    archive = CArchiveReader(str(root / "HomeServer.exe"))
    # The bootloader extracts binaries, data, embedded zip files and splash data.
    unpacked = sum(entry[2] for entry in archive.toc.values() if entry[4] in ("b", "x", "Z", "l"))
    if unpacked <= 0:
        raise ValueError("Executable has no measurable one-file runtime")
    sizes = [p.stat().st_size for p in files]
    payload, largest = sum(sizes), max(sizes)
    if payload <= 0 or payload + unpacked + 2 * largest + 3 * MARGIN >= 2**53:
        raise ValueError("Invalid or excessive payload size")
    with (root / "HomeServer.exe").open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "contract": "vp3.installer.space.v1",
        "payload_bytes": payload,
        "largest_file_bytes": largest,
        "runtime_extraction_bytes": unpacked,
        "install_budget_bytes": payload + largest + MARGIN,
        "temporary_budget_bytes": unpacked + largest + MARGIN,
        "data_headroom_bytes": MARGIN,
        "safety_margin_bytes": MARGIN,
        "file_count": len(files),
        "components": {name: sum(p.stat().st_size for p in files if p.is_relative_to(root / "tools" / name))
                       for name in ("browser", "ffmpeg")},
        "application_sha256": digest,
        "policy": "Conservative additional free-space budgets; existing files receive no overwrite credit. "
                  "Includes largest-file staging allowances and 64 MiB per-role headroom. "
                  "Future user media, models, backups and data migrations are additional.",
    }


def write(root: Path, include: Path, report: Path) -> dict:
    # Measure completely before replacing an earlier build's metadata.
    data = measure(root)
    fields = {"PayloadBytes": "payload_bytes", "InstallBudgetBytes": "install_budget_bytes",
              "TemporaryBudgetBytes": "temporary_budget_bytes", "DataHeadroomBytes": "data_headroom_bytes",
              "RuntimeExtractionBytes": "runtime_extraction_bytes"}
    include.parent.mkdir(parents=True, exist_ok=True)
    include.write_text("// Generated from this build; do not hand-edit.\n" + "".join(
        f'#define {name} "{data[key]}"\n' for name, key in fields.items()), encoding="utf-8")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--include", type=Path, default=Path("installer/SpaceBudget.iss"))
    parser.add_argument("--report", type=Path, default=Path("dist/INSTALL_SPACE.json"))
    args = parser.parse_args()
    print(json.dumps(write(args.dist, args.include, args.report), indent=2))

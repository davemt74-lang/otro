"""Actual CArchive measurements: compression, changing payloads and missing files."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_space_budget", ROOT / "scripts/installer_space_budget.py")
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)


def fixture(root):
    from PyInstaller.archive.writers import CArchiveWriter
    root.mkdir(parents=True, exist_ok=True)
    dll = root / "fixture.dat"
    dll.write_bytes(b"a" * 2 * 1024 * 1024)
    CArchiveWriter(str(root / "HomeServer.exe"), [("payload.dll", str(dll), True, "b")], "python312.dll")
    dll.unlink()
    for name in ("browser/manifest.json", "browser/chrome.exe", "ffmpeg/manifest.json", "ffmpeg/ffmpeg.exe", "ffmpeg/ffprobe.exe"):
        p = root / "tools" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fixture")
    (root / "tools/browser/manifest.json").write_text(json.dumps({"playwright": "1.58.0", "executable": "chrome.exe"}))
    (root / "tools/ffmpeg/manifest.json").write_text(json.dumps({"version": "fixture-1"}))


class SpaceBudgetTests(unittest.TestCase):
    def test_actual_uncompressed_runtime_and_staged_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dist"; fixture(root)
            b = budget.measure(root)
            actual = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
            self.assertEqual(b["runtime_extraction_bytes"], 2 * 1024 * 1024)
            self.assertEqual(b["payload_bytes"], actual)
            self.assertGreater(b["runtime_extraction_bytes"], b["payload_bytes"])
            self.assertEqual(b["install_budget_bytes"], actual + b["largest_file_bytes"] + budget.MARGIN)
            budget.write(root, Path(tmp) / "SpaceBudget.iss", root / "INSTALL_SPACE.json")
            self.assertIn(str(b["temporary_budget_bytes"]), (Path(tmp) / "SpaceBudget.iss").read_text())
            # Metadata outside [Files] must never accumulate into the next build.
            self.assertEqual(budget.measure(root)["payload_bytes"], actual)
            (root / "tools/browser/chrome.exe").write_bytes(b"b" * 100000)
            self.assertEqual(budget.measure(root)["payload_bytes"], actual + 100000 - 7)

    def test_missing_or_unmeasurable_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            (root / "tools/ffmpeg/ffprobe.exe").unlink()
            with self.assertRaises(ValueError): budget.measure(root)
            fixture(root)
            (root / "HomeServer.exe").write_bytes(b"broken archive")
            with self.assertRaises(Exception): budget.measure(root)

    @unittest.skipIf(sys.platform == "win32", "symlink creation requires developer mode on Windows")
    def test_symlink_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            (root / "tools/browser/alias").symlink_to(root / "HomeServer.exe")
            with self.assertRaises(ValueError): budget.measure(root)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture":
        fixture(Path(sys.argv[2]))
    else:
        unittest.main()

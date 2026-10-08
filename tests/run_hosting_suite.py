from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tests = sorted(
    path for path in (ROOT / "tests").glob("hosting_*.py")
    if path.name != "run_hosting_suite.py"
)
if not tests:
    raise SystemExit("No Hosting regression tests were discovered.")

print(f"Discovered {len(tests)} Hosting regression tests")
for path in tests:
    relative = path.relative_to(ROOT)
    print(f"\n=== {relative} ===", flush=True)
    completed = subprocess.run([sys.executable, str(path)], cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

print(f"\nHosting section suite: {len(tests)}/{len(tests)} passed")

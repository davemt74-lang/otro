"""CI-G1: guard staged verification against weakening final release gates."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
BROAD = {
    "homeserver-pr-core.yml": "core",
    "homeserver-v24-release.yml": "acceptance",
    "homeserver-v24-data-continuity.yml": "federation",
    "homeserver-apps-v100.yml": "apps-foundation",
    "native-workspaces.yml": "acceptance",
}
for filename, job in BROAD.items():
    source = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "pull_request:" in source, filename
    assert "types: [opened, reopened, synchronize, ready_for_review]" in source, filename
    assert f"  {job}:\n" in source, filename
    assert "if: ${{ github.event_name != 'pull_request' || github.event.pull_request.draft == false }}" in source, filename
    assert "cancel-in-progress: false" not in source or filename != "homeserver-pr-core.yml", filename
for filename in ("homeserver-v24-release.yml", "homeserver-v24-data-continuity.yml"):
    source = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "  push:\n    branches:\n      - main" in source, filename
    assert "  workflow_dispatch:" in source, filename
ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
assert "branches: [main]" in ci
assert "cancel-in-progress: false" in ci
assert "HomeServer-Windows" in ci
assert "Verify binaries, browser certification and source provenance" in ci
stonefellow = (WORKFLOWS / "stonefellow-native-84b7.yml").read_text(encoding="utf-8")
assert "pull_request:" in stonefellow and "push:" in stonefellow
assert "stonefellow_native_84b7.py" in stonefellow and "windows-latest" in stonefellow
mission = (WORKFLOWS / "agent-mission-tools-a5c.yml").read_text(encoding="utf-8")
assert "      - 'app/**'" not in mission
assert "app/services/agent_mission*.py" in mission
legacy = (WORKFLOWS / "homeserver-v23-release.yml").read_text(encoding="utf-8")
assert "app/services/local_voice.py" in legacy and "installer/**" in legacy
assert "  workflow_dispatch:" in legacy

# The PR Core smoke must stay available on every draft/non-draft PR.
core = (WORKFLOWS / "homeserver-pr-core.yml").read_text(encoding="utf-8")
assert "  smoke:\n    if: github.event_name == 'pull_request'" in core
assert "scripts/ci_pr_smoke.py" in core
assert "fetch-depth: 0" in core
import importlib.util
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("ci_pr_smoke", ROOT / "scripts/ci_pr_smoke.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with patch("pathlib.Path.is_file", return_value=True):
    smoke = module.checks(["file.py", "file.js", "file.mjs", "data.json", "README.md"])
assert len(smoke) == 4, smoke
assert any(cmd[-1] == "file.py" for cmd in smoke)
assert any(cmd[:2] == ["node", "--check"] for cmd in smoke)
for unsafe in ("../secret.py", "/tmp/evil.js"):
    try:
        module.checks([unsafe])
    except ValueError:
        pass
    else:
        raise AssertionError("CI smoke did not reject path traversal: " + unsafe)
try:
    module.diff_files("wrong-revision")
except ValueError:
    pass
else:
    raise AssertionError("Missing PR base must fail closed")
print("CI-G1: staged PR gates, focused triggers and complete main certification verified")

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


bridge = read("app/bridge.py")
remote = read("app/services/remote_bridge.py")
tools_api = read("app/tools_api.py")
federation = read("app/services/approval_federation.py")
file_approvals = read("app/services/local_file_actions_approvals.py")

checks = {
    "canonical tool.execute is advertised": '"tool.execute"' in bridge,
    "legacy tools.execute remains advertised": '"tools.execute"' in bridge,
    "legacy tools.execute normalizes to canonical operation": '"tools.execute": "tool.execute"' in remote,
    "approval list is advertised": '"action.list"' in bridge and 'if op == "action.list"' in remote,
    "approval status is advertised": '"action.status"' in bridge and 'if op == "action.status"' in remote,
    "approval review operations are advertised": '"action.approve"' in bridge and '"action.deny"' in bridge,
    "remote dispatcher preserves action review operations": 'if op in {"action.approve", "action.deny"}' in remote,
    "write execution still uses owner-defined action policy": "APPROVAL_REQUIRED" in tools_api and "SAFE_AUTOMATIC" in tools_api,
    "sensitive high-impact tools remain blocked remotely": "SENSITIVE_HIGH_IMPACT" in tools_api and "can only be run from local HomeServer owner control" in tools_api,
    "physical device approval stays local owner only": "LOCAL_OWNER_ONLY_ACTIONS" in federation and "local HomeServer owner approval" in federation,
    "governed file approval stays local owner only": "Governed file mutations require approval from local HomeServer owner control" in file_approvals,
    "capability contract explicitly preserves approval boundaries": '"approval_boundaries_preserved": True' in bridge,
}

for name, ok in checks.items():
    assert ok, name
    print("PASS", name)

print(f"HomeServer v2.3 Section 4 governed actions: {len(checks)}/{len(checks)} passed")

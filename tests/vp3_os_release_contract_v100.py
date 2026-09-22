from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

vp3_os = (ROOT / "app" / "services" / "vp3_os.py").read_text(encoding="utf-8")
readiness = (ROOT / "app" / "services" / "release_readiness.py").read_text(encoding="utf-8")
readiness_api = (ROOT / "app" / "release_readiness_api.py").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
workflow = (ROOT / ".github" / "workflows" / "vp3-os-production-release-v100.yml").read_text(encoding="utf-8")
v090_workflow = (ROOT / ".github" / "workflows" / "vp3-os-ambient-orchestration-v090.yml").read_text(encoding="utf-8")
policy = (ROOT / ".github" / "CI_POLICY.md").read_text(encoding="utf-8")
docs = (ROOT / "docs" / "VP3_OS_PRODUCTION_RELEASE_V100.md").read_text(encoding="utf-8")

assert 'VP3_OS_VERSION = "v1.0"' in vp3_os
for required in (
    '"local_automation": "vp3_os_local_automation_v070"',
    '"automation_intelligence": "vp3_os_automation_intelligence_v080"',
    '"ambient_orchestration": "vp3_os_ambient_orchestration_v090"',
    '"production_release": "vp3_os_production_release_v100"',
):
    assert required in vp3_os, required

for forbidden in (
    "execute_command(",
    "approve_request(",
    "create_device_command_request(",
):
    assert forbidden not in readiness, forbidden
    assert forbidden not in readiness_api, forbidden

for required in (
    'RELEASE_VERSION = "v1.0"',
    "MIN_SCHEMA_VERSION = 26",
    '"database"',
    '"data_directory"',
    '"owner_security"',
    '"backup_recovery"',
    '"hardware"',
    '"physical_action_governance"',
    '"production_ready"',
    '"post_merge_validation": True',
):
    assert required in readiness, required

assert '"/api/v1/control/vp3-os/release-readiness"' in readiness_api
assert "release_readiness.report()" in readiness_api
assert "release_readiness_router" in bridge
assert '"vp3_os_release_readiness": release_readiness.public_capability()' in bridge
for feature in (
    '"vp3.os.v100"',
    '"vp3.os.production_release.v1"',
    '"vp3.os.release_readiness.v1"',
):
    assert feature in bridge, feature

assert "pull_request:" in workflow
assert "cancel-in-progress: true" in workflow
assert "ubuntu-latest" in workflow
assert "windows-latest" in workflow
assert "tests/vp3_os_release_v100.py" in workflow
assert "tests/vp3_os_soak_v100.py" in workflow
assert "tests/vp3_os_release_contract_v100.py" in workflow
assert "tests/recovery.py" in workflow
assert "tests/backup_security.py" in workflow
assert "tests/migrations.py" in workflow

assert "workflow_dispatch:" in v090_workflow
assert "pull_request:" not in v090_workflow
assert "push:" not in v090_workflow

assert "current VP3 OS release workflow" in policy
assert "No new VP3 OS phase starts until" in policy

for required in (
    "No new product features",
    "fresh install",
    "upgrade",
    "bounded soak",
    "zero unapproved physical actions",
    "post-merge",
):
    assert required.lower() in docs.lower(), required

print("VP3 OS v1.0 production release contract passed")

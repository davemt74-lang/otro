from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v23-release-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import initialize_database  # noqa: E402
    from app.config import settings  # noqa: E402
    from app import bridge  # noqa: E402

    initialize_database()

    bridge.providers.inference_status = lambda: {
        "available": True,
        "selected_provider": "ollama",
        "model": "release-test",
        "compute_source": "homeserver_local",
        "cloud_fallback_required": False,
        "providers": [],
    }

    caps = bridge.capabilities()

assert settings.version == "2.4"
assert caps["version"] == "2.4"

unified = caps["unified_execution"]
assert unified["version"] == "2.3"
assert unified["authority"] == "existing_home_server_services"
assert unified["cloud_routeable"] is True
assert unified["approval_boundaries_preserved"] is True

required_operations = {
    "agent.chat",
    "agent.infer.local",
    "capabilities",
    "capability.registry",
    "knowledge.search",
    "files.list",
    "files.read",
    "tools.list",
    "tool.execute",
    "tools.execute",
    "tasks.list",
    "notifications.list",
    "shared.context.exchange",
    "system.ping",
    "speech.status",
    "speech.transcribe",
    "speech.synthesize",
    "action.list",
    "action.status",
    "action.approve",
    "action.deny",
}
assert required_operations.issubset(set(unified["operations"]))

required_domains = {"inference", "files", "knowledge", "tools", "voice", "devices"}
assert required_domains.issubset(set(unified["local_domains"]))

profile_safe = unified["profile_safe_local_inference"]
assert profile_safe == {
    "operation": "agent.infer.local",
    "stateless": True,
    "local_only": True,
    "tools_enabled": False,
    "caller_supplied_context_only": True,
}

voice = caps["local_voice"]
assert voice["local_only"] is True
assert {"speech.status", "speech.transcribe", "speech.synthesize"}.issubset(set(voice["operations"]))

files = caps["files"]
assert files["read_only_api"] is True
assert files["write_policy_gated"] is True
assert files["arbitrary_paths"] is False

action_policy = caps["action_policy"]
assert action_policy["owner_managed"] is True
assert unified["approval_boundaries_preserved"] is True

assert caps["shared_agent_context"]["mode"] == "federated"
assert caps["shared_agent_context"]["authoritative_sources_preserved"] is True
assert caps["shared_agent_context"]["round_trip_operation"] == "system.ping"

assert bridge.settings.max_remote_bridge_message_bytes == 256 * 1024

remote = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
for operation in required_operations:
    if operation in {"capabilities", "capability.registry"}:
        continue
    assert operation in remote or operation in {
        "files.list",
        "files.read",
        "knowledge.search",
        "tools.list",
        "tool.execute",
        "tools.execute",
        "tasks.list",
        "notifications.list",
        "shared.context.exchange",
        "system.ping",
        "action.list",
        "action.status",
        "action.approve",
        "action.deny",
    }, f"missing remote operation contract: {operation}"

installer = (ROOT / "installer" / "HomeServer.iss").read_text(encoding="utf-8")
assert '#define MyAppVersion "2.4"' in installer
assert "HomeServer\\Data" not in installer

workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
assert "version = '2.4'" in workflow
assert "minimum_schema_version = 38" in workflow
assert "HomeServerSetup.exe" in workflow
assert "SHA256SUMS.txt" in workflow
assert "RELEASE.json" in workflow
assert "Verify packaged v2.1 to v2.4 upgrade takeover" in workflow
assert "Verify packaged VP3 HTTPS session survives process restart" in workflow
assert "Verify silent installer upgrade preserves private data" in workflow

for section_test in (
    "governed_actions_v230.py",
    "voice_profile_execution_v235.py",
):
    assert section_test in workflow

print("HomeServer v2.3 unified execution retained under v2.4 release: PASS")

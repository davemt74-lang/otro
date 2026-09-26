from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v24-release-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
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
    with db() as connection:
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

assert settings.version == "2.4"
assert caps["version"] == "2.4"
assert versions == list(range(1, 40))

legacy = caps["unified_execution"]
assert legacy["version"] == "2.3"
assert legacy["cloud_routeable"] is True
assert legacy["approval_boundaries_preserved"] is True

federation = caps["federated_data_continuity"]
assert federation["version"] == "2.4"
assert federation["mode"] == "native_authority_mirrored_continuity"
assert federation["native_source_remains_authoritative"] is True
assert federation["remote_records_are_mirrors"] is True
assert federation["no_cross_database_id_writes"] is True
assert federation["canonical_identity"] == "sha256(authority_source|dataset|authority_key)"
assert federation["conflict_resolution"] == "authority_wins"

contacts = caps["contacts_continuity"]
assert contacts["version"] == "2.4"
assert contacts["canonical_mutations"] is True
assert contacts["governed_writes"] is True
assert contacts["approval_required_by_default"] is True
assert contacts["remote_native_id_mutations"] is False

knowledge = caps["knowledge_continuity"]
assert knowledge["version"] == "2.4"
assert knowledge["canonical_mutations"] is True
assert knowledge["governed_writes"] is True
assert knowledge["optimistic_concurrency"] is True
assert knowledge["idempotent_mutations"] is True
assert knowledge["absolute_paths_exposed"] is False
assert knowledge["remote_native_id_mutations"] is False

tasks = caps["task_calendar_continuity"]
assert tasks["version"] == "2.4"
assert set(tasks["datasets"]) == {"tasks", "calendar"}
assert tasks["canonical_mutations"] is True
assert tasks["governed_writes"] is True
assert tasks["optimistic_concurrency"] is True
assert tasks["idempotent_mutations"] is True
assert tasks["remote_native_id_mutations"] is False

files = caps["file_document_continuity"]
assert files["version"] == "2.4"
assert files["canonical_identity"] is True
assert files["governed_writes"] is True
assert files["local_owner_approval_only"] is True
assert files["optimistic_concurrency"] is True
assert files["idempotent_mutations"] is True
assert files["opaque_refs"] is True
assert files["absolute_paths_exposed"] is False
assert files["remote_native_id_mutations"] is False

memory = caps["agent_brain_memory_continuity"]
assert memory["version"] == "2.4"
assert memory["canonical_identity"] is True
assert memory["provenance_preserved"] is True
assert memory["governed_writes"] is True
assert memory["approval_required_by_default"] is True
assert memory["optimistic_concurrency"] is True
assert memory["idempotent_mutations"] is True
assert memory["memory_key_scopes_enforced"] is True
assert memory["candidate_promotion_preserved"] is True
assert memory["remote_records_are_mirrors"] is True
assert memory["remote_native_id_mutations"] is False

reconcile = caps["disconnect_reconnect_reconciliation"]
assert reconcile["version"] == "2.4"
assert reconcile["contract"] == "v246"
assert reconcile["mode"] == "full_snapshot_authority_reconciliation"
assert reconcile["native_source_remains_authoritative"] is True
assert reconcile["absence_tombstones_full_snapshots_only"] is True
assert reconcile["filtered_snapshots_never_delete"] is True
assert reconcile["reconnect_requires_reconciliation"] is True
assert reconcile["revision_integrity"] is True
assert reconcile["duplicate_authority_keys_rejected"] is True
assert reconcile["durable_reconciliation_runs"] is True
assert reconcile["agent_brain_status"] is True
assert reconcile["remote_native_id_mutations"] is False

release = caps["v24_release_acceptance"]
required_sections = {
    "federated_data_continuity",
    "contacts_continuity",
    "knowledge_continuity",
    "task_calendar_continuity",
    "file_document_continuity",
    "agent_brain_memory_continuity",
    "disconnect_reconnect_reconciliation",
}
assert release["version"] == "2.4"
assert release["contract"] == "v247"
assert set(release["sections"]) == required_sections
assert release["minimum_schema_version"] == 38
assert release["retains_v23_unified_execution"] is True
assert release["windows_portable_exe"] is True
assert release["windows_installer"] is True
assert release["upgrade_preserves_private_data"] is True
assert release["release_manifest"] == "vp3-os-release-v1"

installer = (ROOT / "installer" / "HomeServer.iss").read_text(encoding="utf-8")
assert '#define MyAppVersion "2.4"' in installer
assert "HomeServer\\Data" not in installer

ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
for section_test in (
    "federated_data_authority_v240.py",
    "contacts_continuity_v241.py",
    "knowledge_continuity_v242.py",
    "task_calendar_continuity_v243.py",
    "file_document_continuity_v244.py",
    "agent_brain_memory_continuity_v245.py",
    "reconnect_reconciliation_v246.py",
    "homeserver_v24_release_acceptance.py",
):
    assert section_test in ci
assert "minimum_schema_version = 38" in ci
assert "version = '2.4'" in ci
assert "HomeServer.exe" in ci
assert "HomeServerSetup.exe" in ci
assert "SHA256SUMS.txt" in ci
assert "RELEASE.json" in ci
assert "Verify packaged v2.1 to v2.4 upgrade takeover" in ci
assert "Verify silent installer upgrade preserves private data" in ci

release_workflow = (ROOT / ".github" / "workflows" / "homeserver-v24-release.yml").read_text(encoding="utf-8")
for section_test in (
    "federated_data_authority_v240.py",
    "contacts_continuity_v241.py",
    "knowledge_continuity_v242.py",
    "task_calendar_continuity_v243.py",
    "file_document_continuity_v244.py",
    "agent_brain_memory_continuity_v245.py",
    "reconnect_reconciliation_v246.py",
    "homeserver_v23_release_acceptance.py",
    "homeserver_v24_release_acceptance.py",
    "installer_contract.py",
):
    assert section_test in release_workflow

print("HomeServer v2.4 Section 8 release acceptance: PASS")

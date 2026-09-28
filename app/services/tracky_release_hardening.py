from __future__ import annotations

from typing import Any

from ..database import db
from . import (
    tracky_federation_access_operations,
    tracky_federation_fleet_health,
    tracky_federation_governed_operations,
    tracky_federation_reconciliation,
    tracky_site_topology,
)

VERSION = "2.80"
PROTOCOL = "physical_federation_v280_release_hardening.v1"
FINAL_SECTION = 10
SCHEMA_VERSION = 53
INVARIANTS = (
    "origin-homeserver-is-authoritative",
    "cloud-is-request-relay-and-read-only-mirror",
    "agent-may-propose-never-execute",
    "reconnect-is-not-recovery",
    "command-success-is-not-recovery",
    "only-authoritative-reconciliation-restores-current",
    "stale-state-never-promotes-to-current",
    "authority-transfer-requires-epoch-advance",
    "old-authority-cannot-write-after-transfer",
    "conflicting-authority-claims-fail-closed",
    "revocation-wins-over-cached-grants",
    "terminal-operation-state-is-immutable",
    "migration-053-is-repeat-safe",
)


def _latest_schema() -> int:
    with db() as connection:
        row = connection.execute("SELECT COALESCE(MAX(version),0) AS version FROM schema_migrations").fetchone()
    return int(row["version"] or 0) if row else 0


def public_capability() -> dict[str, Any]:
    reconciliation = tracky_federation_reconciliation.public_capability()
    operations = tracky_federation_governed_operations.public_capability()
    access = tracky_federation_access_operations.public_capability()
    fleet = tracky_federation_fleet_health.public_capability()
    topology = tracky_site_topology.public_capability()
    checks = {
        "schema_053_current": _latest_schema() == SCHEMA_VERSION,
        "origin_authority_only": reconciliation.get("authority_assignment") == "origin_only",
        "cloud_relay_mirror_only": reconciliation.get("cloud_role") == "relay_and_mirror_only",
        "same_revision_conflict_fail_closed": reconciliation.get("same_revision_conflicts") == "fail_closed",
        "revocation_wins": access.get("revocation_wins") is True and operations.get("revocation_wins") is True,
        "cloud_execution_blocked": operations.get("cloud_execution_allowed") is False,
        "agent_execution_blocked": operations.get("agent_proposal_only") is True,
        "authority_epoch_required": operations.get("authority_transfer_requires_epoch_advance") is True,
        "reconciliation_required_for_recovery": operations.get("completion_requires_authoritative_reconciliation") is True,
        "diagnostics_observational": fleet.get("authority_mutation") is False,
        "topology_local_authority": topology.get("authority_assignment") in {"local_only", "origin_only"},
    }
    return {
        "version": VERSION,
        "protocol": PROTOCOL,
        "final_section": FINAL_SECTION,
        "schema_version": SCHEMA_VERSION,
        "golden_scenarios": 24,
        "invariants": list(INVARIANTS),
        "checks": checks,
        "release_ready": all(checks.values()),
        "split_brain_allowed": False,
        "stale_current_promotion_allowed": False,
        "revocation_resurrection_allowed": False,
        "cloud_execution_allowed": False,
        "agent_execution_allowed": False,
        "recovery_authority": "section7_authoritative_reconciliation",
    }


def cloud_projection() -> dict[str, Any]:
    report = public_capability()
    return {
        "protocol": report["protocol"],
        "version": report["version"],
        "final_section": report["final_section"],
        "schema_version": report["schema_version"],
        "golden_scenarios": report["golden_scenarios"],
        "release_ready": report["release_ready"],
        "checks": report["checks"],
        "summary_only": True,
        "cloud_read_only": True,
        "remote_command_execution": False,
        "authority_mutation": False,
    }

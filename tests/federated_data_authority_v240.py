from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v240-federation-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import federated_data, shared_agent_context  # noqa: E402
    from app.services.pairing import approve_pairing_request, create_pairing_request  # noqa: E402
    from app.services.remote_bridge import dispatch_remote_request  # noqa: E402

    initialize_database()

    assert federated_data.FEDERATED_DATA_VERSION == "2.4"
    assert set(federated_data.DATASETS) == {
        "memory", "knowledge", "contacts", "tasks", "notifications", "profile_context"
    }

    cloud_id = federated_data.canonical_id("vp3_cloud", "contacts", "contacts:42")
    cloud_id_again = federated_data.canonical_id("vp3_cloud", "contacts", "contacts:42")
    home_id = federated_data.canonical_id("homeserver", "contacts", "contacts:42")
    assert cloud_id == cloud_id_again
    assert cloud_id.startswith("fd24_")
    assert cloud_id != home_id

    cloud_record = federated_data.envelope(
        "vp3_cloud",
        "contacts",
        "contacts:42",
        title="Cloud contact",
        content="Authoritative in VP3 Cloud",
        updated_at="2026-09-25T12:00:00Z",
    )
    assert cloud_record["canonical_id"] == cloud_id
    assert cloud_record["mirror_only"] is True

    home_record = federated_data.envelope(
        "homeserver",
        "tasks",
        "7",
        title="Local task",
        content="Authoritative on HomeServer",
    )
    assert home_record["mirror_only"] is False

    snapshot = {
        "version": shared_agent_context.SHARED_AGENT_CONTEXT_VERSION,
        "revision": "cloud-rev-1",
        "generated_at": "2026-09-25T12:00:00Z",
        "authoritative_source": "vp3_cloud",
        "datasets": {
            "memory": [],
            "knowledge": [],
            "contacts": [cloud_record],
            "tasks": [],
            "notifications": [],
        },
    }
    applied = shared_agent_context.apply_cloud_snapshot(snapshot)
    assert applied["source"] == "vp3_cloud"
    assert applied["revision"] == "cloud-rev-1"

    mirrored = shared_agent_context.cloud_snapshot()
    assert mirrored is not None
    mirrored_contact = mirrored["datasets"]["contacts"][0]
    assert mirrored_contact["canonical_id"] == cloud_id
    assert mirrored_contact["authority_source"] == "vp3_cloud"
    assert mirrored_contact["mirror_only"] is True

    with db() as connection:
        links = [dict(row) for row in connection.execute(
            "SELECT authority_source,dataset,authority_key,canonical_id,observed_source "
            "FROM federated_record_links ORDER BY authority_source,dataset"
        ).fetchall()]
        cursors = [dict(row) for row in connection.execute(
            "SELECT peer_source,dataset,revision FROM federated_sync_cursors ORDER BY dataset"
        ).fetchall()]
    assert any(
        row["authority_source"] == "vp3_cloud"
        and row["dataset"] == "contacts"
        and row["canonical_id"] == cloud_id
        and row["observed_source"] == "homeserver"
        for row in links
    )
    assert any(
        row["peer_source"] == "vp3_cloud"
        and row["dataset"] == "contacts"
        and row["revision"] == "cloud-rev-1"
        for row in cursors
    )

    registry = federated_data.registry()
    assert registry["version"] == "2.4"
    assert registry["mode"] == "native_authority_mirrored_continuity"
    assert registry["account_scoped_identity"] is True
    assert registry["rules"]["native_source_remains_authoritative"] is True
    assert registry["rules"]["remote_records_are_mirrors"] is True
    assert registry["rules"]["no_cross_database_id_writes"] is True
    assert registry["rules"]["conflict_resolution"] == "authority_wins"
    assert registry["mirror_link_count"] >= 1

    pair = create_pairing_request("vp3", "VP3", ["agent.chat"])
    approved = approve_pairing_request(pair["request_id"])
    assert approved is not None
    token = str(pair["claim_token"])

    remote = dispatch_remote_request("federation.registry", {}, token)
    assert remote["ok"] is True
    assert remote["payload"]["version"] == "2.4"
    assert remote["payload"]["rules"]["no_cross_database_id_writes"] is True

    invalid = dict(cloud_record)
    invalid["canonical_id"] = "fd24_" + ("0" * 40)
    try:
        federated_data.normalize_envelope(
            invalid,
            default_source="vp3_cloud",
            dataset="contacts",
        )
        raise AssertionError("mismatched canonical identity was accepted")
    except federated_data.FederatedDataError:
        pass

print("HomeServer v2.4 Section 1 federated data authority: PASS")

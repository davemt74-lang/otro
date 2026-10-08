"""Normal and saved legacy VP3 pairings reconcile only authorized native data."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="shared-context-permissions-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    from app.database import db, initialize_database
    from app.services import cloud_pairing, contacts, federated_data, file_continuity, pairing, remote_bridge

    initialize_database()
    contacts.create_contact({"display_name": "Native contact"})
    datasets = ("memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications")
    cloud = {"authoritative_source": "vp3_cloud", "snapshot_mode": "full",
             "covered_datasets": list(datasets), "revision": "cloud-permission-test",
             "datasets": {name: [] for name in datasets}}

    # Match the installed failure: normal saved pairing from before this repair.
    legacy_permissions = [p for p in cloud_pairing._VP3_PERMISSIONS if p != "files.read"]
    request = pairing.create_pairing_request("vp3", "VP3", legacy_permissions)
    pairing.approve_pairing_request(request["request_id"])
    token = request["claim_token"]
    before = pairing.authenticate(token)
    with patch.object(file_continuity, "list_federated_files", side_effect=AssertionError("Ungranted files were read")):
        reply = remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud}, token)
        assert reply["ok"], reply
        home = reply["payload"]["homeserver_snapshot"]
        assert home["snapshot_mode"] == "full"
        assert "files" not in home["covered_datasets"]
        assert home["datasets"]["files"] == []
        assert home["unavailable_datasets"] == {"files": "permission_required:files.read"}
        assert home["datasets"]["contacts"][0]["title"] == "Native contact"
        assert reply["payload"]["reconciliation"]["cloud_to_homeserver"]["status"] == "completed"
        filtered = remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud, "query": "Native"}, token)
        assert filtered["payload"]["homeserver_snapshot"]["snapshot_mode"] == "filtered"
    assert pairing.authenticate(token)["permissions"] == before["permissions"]
    assert "files.read" not in pairing.authenticate(token)["permissions"]
    print("PASS saved pairing reconciles authorized context without reading files or changing grants")

    # New pairing must use the production permission list, not a hand-made list.
    request = pairing.create_pairing_request("vp3", "VP3", cloud_pairing._VP3_PERMISSIONS)
    pairing.approve_pairing_request(request["request_id"])
    token = request["claim_token"]
    assert "files.read" in pairing.authenticate(token)["permissions"]
    native_file = {"name": "Native file", "authority_source": "homeserver", "authority_key": "file:permission-test", "size_bytes": 12}
    with patch.object(file_continuity, "list_federated_files", return_value=[native_file]) as read_files:
        reply = remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud}, token)
        home = reply["payload"]["homeserver_snapshot"]
        assert read_files.call_count == 1
        assert "files" in home["covered_datasets"]
        assert home["datasets"]["files"][0]["title"] == "Native file"
        assert home["unavailable_datasets"] == {}
        app_id = pairing.authenticate(token)["id"]
        with db() as connection:
            connection.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='files.read'", (app_id,))
        read_files.side_effect = AssertionError("Revoked files were read")
        reply = remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud}, token)
        assert reply["ok"] and "files" not in reply["payload"]["homeserver_snapshot"]["covered_datasets"]
        assert read_files.call_count == 1
    print("PASS normal pairing includes files; live permission revocation excludes them immediately")

    pairing.revoke_paired_app("vp3")
    try:
        remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud}, token)
        raise AssertionError("Revoked pairing was accepted")
    except remote_bridge.RemoteBridgeError:
        pass
    limited = pairing.create_pairing_request("limited", "Limited", ["memory.read"])
    pairing.approve_pairing_request(limited["request_id"])
    try:
        remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud}, limited["claim_token"])
        raise AssertionError("Insufficient core permissions were accepted")
    except remote_bridge.RemoteBridgeError:
        pass
    print("PASS revoked and insufficient pairings stay denied")

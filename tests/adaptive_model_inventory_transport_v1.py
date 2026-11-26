"""Opt-in private model inventory: real SQLite pairing, mocked local HTTP/discovery."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="adaptive-inventory-transport-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from app.database import initialize_database
    from app.services import pairing, remote_bridge as bridge, adaptive_model_inventory_v1 as inventory

    initialize_database()

    def paired(key, permissions):
        request = pairing.create_pairing_request(key, key, permissions)
        pairing.approve_pairing_request(request["request_id"])
        return request["claim_token"]

    vp3 = paired("vp3", ["agent.chat"])
    other = paired("inventory-test-app", ["agent.chat"])
    snapshot = inventory.inventory_snapshot(
        {"enabled": True, "model": "private-model:latest"},
        {"reachable": True, "models": ["private-model:latest"]},
    )
    legacy = {"available": True, "model": "configured", "app": "vp3"}

    class Response:
        status_code = 200
        def json(self):
            return dict(legacy)

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, path, **kwargs):
            assert path == "/api/v1/inference/status"
            return Response()

    def denied(token):
        try:
            bridge.dispatch_remote_request("inference.status", {"include_model_inventory": True}, token)
        except bridge.RemoteBridgeError:
            return
        raise AssertionError("Unauthorized inventory request succeeded")

    with patch.object(bridge.httpx, "Client", Client), patch.object(inventory, "collect_inventory", return_value=snapshot) as collect:
        for body in ({}, {"include_model_inventory": False}, {"include_model_inventory": "true"}):
            result = bridge.dispatch_remote_request("inference.status", body, vp3)
            assert result["payload"] == legacy
        collect.assert_not_called()
        denied(None)
        denied("invalid-token-" * 3)
        denied(other)
        collect.assert_not_called()
        result = bridge.dispatch_remote_request("inference.status", {"include_model_inventory": True, "model": "arbitrary"}, vp3)
        assert result["status"] == 422 and not result["ok"]
        collect.assert_not_called()
        result = bridge.dispatch_remote_request("inference.status", {"include_model_inventory": True}, vp3)
        assert result["ok"] and result["payload"]["model_inventory"] == snapshot
        assert result["payload"]["model_inventory"]["models"][0]["routing_eligible"] is False
        collect.assert_called_once()

        def revoke_during_discovery():
            pairing.revoke_paired_app("vp3")
            return snapshot

        collect.side_effect = revoke_during_discovery
        denied(vp3)
        collect.reset_mock(side_effect=True)
        denied(vp3)
        collect.assert_not_called()

        no_chat = paired("vp3", ["memory.read"])
        denied(no_chat)
        collect.assert_not_called()

print("Adaptive model inventory transport contract passed")

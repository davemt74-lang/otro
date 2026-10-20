from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import food_monster_connector as fm

_MIGRATION = Path(__file__).resolve().parents[1] / "database" / "food_monster_connections.sql"


class FoodMonsterConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / "testing.db"
        with sqlite3.connect(self.database) as conn:
            conn.executescript(_MIGRATION.read_text(encoding="utf-8"))

        @contextlib.contextmanager
        def testing_db():
            connection = sqlite3.connect(self.database)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        self.calls = []
        self.identities = {
            "https://gelato.vp3.me": "11111111-1111-1111-1111-111111111111",
            "https://other.foodmonster.xyz": "22222222-2222-2222-2222-222222222222",
        }

        def remote(origin, payload=None, token=None):
            self.calls.append((origin, payload, token))
            identity = self.identities[origin]
            if payload is None:
                return {"ok": True, "schema": "food-monster-vp3-v1",
                        "instanceId": identity, "capabilities": ["knowledge.search"]}
            if payload["action"] == "exchange":
                return {"ok": True, "schema": "food-monster-vp3-v1",
                        "instanceId": identity, "connectionId": "a" * 32,
                        "accessToken": "fm2_" + "A" * 43,
                        "scopes": ["menu"]}
            if payload["action"] == "revoke":
                return {"ok": True, "schema": "food-monster-vp3-v1",
                        "revoked": True, "instanceId": identity,
                        "connectionId": "a" * 32}
            if payload["action"] == "search":
                return {"ok": True, "schema": "food-monster-knowledge-v1",
                        "results": [{"type": "menu", "id": "gelato:menu:12",
                                     "title": "Margherita Pizza",
                                     "source": {"kind": "active_menu_item"}}]}
            raise AssertionError("Unexpected remote operation")

        for target, value in [
            ("db", testing_db),
            ("settings", SimpleNamespace(data_dir=self.directory)),
            ("_request", remote),
        ]:
            p = patch.object(fm, target, value)
            p.start()
            self.addCleanup(p.stop)

    def test_origin_validation_prevents_ssrf(self):
        self.assertEqual(fm.normalize_origin(" HTTPS://GELATO.VP3.ME/ ".strip().lower()),
                         "https://gelato.vp3.me")
        for invalid in [
            "http://gelato.vp3.me", "https://localhost", "https://127.0.0.1",
            "https://10.1.2.3", "https://router.local", "https://user:secret@host.example",
            "https://gelato.vp3.me:8443", "https://gelato.vp3.me/api",
            "https://gelato.vp3.me?x=1", "https://gelato.vp3.me/#token",
            "https://gelato.vp3.me:bad", "https://example..com",
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(fm.FoodMonsterLinkError):
                fm.normalize_origin(invalid)

    def test_public_ip_rule(self):
        import socket
        with patch.object(fm.socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.12", 443)),
        ]):
            # Documentation IP addresses are not routed globally.
            with self.assertRaises(fm.FoodMonsterLinkError):
                fm.require_public_dns("https://gelato.vp3.me")
        with patch.object(fm.socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ]):
            fm.require_public_dns("https://gelato.vp3.me")

    def test_pairing_and_search_scoped_to_origin(self):
        code = "fmlink_" + "B" * 43
        item = fm.connect_instance("https://gelato.vp3.me/", code, "Gelato")["connection"]
        self.assertEqual(item["origin"], "https://gelato.vp3.me")
        self.assertEqual(item["scopes"], ["menu"])
        self.assertNotIn("accessToken", json.dumps(item))
        self.assertNotIn("pairingCode", json.dumps(item))
        self.assertNotIn("fm2_", self.database.read_bytes().decode("utf-8", errors="ignore"))
        credential_file = fm._vault_path()
        self.assertTrue(credential_file.exists())
        if os.name != "nt":
            self.assertEqual(credential_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(fm.list_connections()), 1)
        result = fm.search_instance(item["id"], "pizza", "menu")
        self.assertEqual(result["origin"], "https://gelato.vp3.me")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["title"], "Margherita Pizza")
        self.assertTrue(any(call[2] == "fm2_" + "A" * 43 for call in self.calls))
        before = len(self.calls)
        with self.assertRaises(fm.FoodMonsterLinkError):
            fm.search_instance(item["id"], "dough", "recipe")
        self.assertEqual(len(self.calls), before, "Forbidden category cannot query remote")
        with self.assertRaises(fm.FoodMonsterLinkError):
            fm.connect_instance("https://gelato.vp3.me", code, "Repeat")
        self.assertEqual(len(self.calls), before + 1, "Duplicate rejected before code exchange")

        second = fm.connect_instance("https://other.foodmonster.xyz", code, "Other")["connection"]
        self.assertEqual(len(fm.list_connections()), 2)
        self.assertNotEqual(item["id"], second["id"])
        self.assertNotEqual(item["instanceId"], second["instanceId"])
        removed = fm.disconnect_instance(item["id"])
        self.assertTrue(removed["disconnected"])
        self.assertTrue(removed["remoteRevoked"])
        self.assertFalse(removed["remoteRevocationRequired"])
        self.assertTrue(any(call[1] == {"action": "revoke"} and call[0] == item["origin"]
                            for call in self.calls))
        self.assertNotIn(item["id"], fm._vault_read())
        self.assertEqual(len(fm.list_connections()), 1)
        with self.assertRaises(fm.FoodMonsterLinkError):
            fm.search_instance(item["id"], "pizza")

    def test_reject_invalid_pairing_and_remote_permissions(self):
        with self.assertRaises(fm.FoodMonsterLinkError):
            fm.connect_instance("https://gelato.vp3.me", "short", "Gelato")
        with self.assertRaises(fm.FoodMonsterLinkError):
            fm.connect_instance("http://gelato.vp3.me", "fmlink_" + "X" * 43, "Gelato")
        with patch.object(fm, "_request", return_value={
            "ok": True, "schema": "food-monster-vp3-v1",
            "instanceId": self.identities["https://gelato.vp3.me"],
            "capabilities": ["knowledge.search"],
        }) as request:
            with self.assertRaises(fm.FoodMonsterLinkError):
                fm.connect_instance("https://gelato.vp3.me", "fmlink_" + "Y" * 43, "Gelato")
            self.assertEqual(request.call_count, 2)
        self.assertEqual(fm.list_connections(), [])

    def test_inconsistent_discovery_rejected(self):
        with patch.object(fm, "_request", return_value={
            "ok": True, "schema": "food-monster-vp3-v1",
            "instanceId": "11111111-1111-1111-1111-111111111111",
            "capabilities": [],
        }):
            with self.assertRaises(fm.FoodMonsterLinkError):
                fm.connect_instance("https://gelato.vp3.me", "fmlink_" + "Y" * 43, "Gelato")

    def test_disconnect_offline_wipes_local_secret_and_warns_manual_revoke(self):
        code = "fmlink_" + "C" * 43
        item = fm.connect_instance("https://gelato.vp3.me", code, "Gelato")["connection"]
        with patch.object(fm, "_request",
                          side_effect=fm.FoodMonsterLinkError("No network", 502)):
            result = fm.disconnect_instance(item["id"])
        self.assertTrue(result["disconnected"])
        self.assertFalse(result["remoteRevoked"])
        self.assertTrue(result["remoteRevocationRequired"])
        self.assertIn("owner approval page", result["message"])
        self.assertNotIn(item["id"], fm._vault_read())
        self.assertEqual(fm.list_connections(), [])
        self.assertNotIn("fm2_", json.dumps(result))

    def test_disconnect_mismatched_remote_identity_not_verified(self):
        code = "fmlink_" + "D" * 43
        item = fm.connect_instance("https://gelato.vp3.me", code, "Gelato")["connection"]
        with patch.object(fm, "_request", return_value={
            "ok": True, "schema": "food-monster-vp3-v1", "revoked": True,
            "instanceId": "00000000-0000-0000-0000-000000000000",
            "connectionId": "a" * 32,
        }) as remote:
            result = fm.disconnect_instance(item["id"])
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(remote.call_args.args[0], item["origin"])
        self.assertEqual(remote.call_args.args[1], {"action": "revoke"})
        self.assertEqual(remote.call_args.kwargs["token"], "fm2_" + "A" * 43)
        self.assertFalse(result["remoteRevoked"])
        self.assertTrue(result["remoteRevocationRequired"])
        self.assertEqual(fm.list_connections(), [])
        self.assertEqual(fm._vault_read(), {})

    def test_disconnect_mismatched_remote_connection_not_verified(self):
        item = fm.connect_instance(
            "https://gelato.vp3.me", "fmlink_" + "E" * 43, "Gelato"
        )["connection"]
        with patch.object(fm, "_request", return_value={
            "ok": True, "schema": "food-monster-vp3-v1", "revoked": True,
            "instanceId": item["instanceId"], "connectionId": "b" * 32,
        }):
            result = fm.disconnect_instance(item["id"])
        self.assertFalse(result["remoteRevoked"])
        self.assertTrue(result["remoteRevocationRequired"])
        self.assertFalse(fm.list_connections())

    def test_disconnection_is_per_restaurant(self):
        first = fm.connect_instance("https://gelato.vp3.me",
                                    "fmlink_" + "F" * 43, "Gelato")["connection"]
        second = fm.connect_instance("https://other.foodmonster.xyz",
                                     "fmlink_" + "G" * 43, "Other")["connection"]
        self.calls.clear()
        result = fm.disconnect_instance(first["id"])
        self.assertTrue(result["remoteRevoked"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], first["origin"])
        self.assertEqual(self.calls[0][1], {"action": "revoke"})
        self.assertEqual([x["id"] for x in fm.list_connections()], [second["id"]])
        self.assertNotIn(first["id"], fm._vault_read())
        self.assertIn(second["id"], fm._vault_read())
        self.assertEqual(fm.search_instance(second["id"], "pizza", "menu")["count"], 1)

    def test_disconnection_without_local_secret_requires_manual_cleanup(self):
        item = fm.connect_instance(
            "https://gelato.vp3.me", "fmlink_" + "H" * 43, "Gelato"
        )["connection"]
        fm._vault_write({})
        self.calls.clear()
        result = fm.disconnect_instance(item["id"])
        self.assertEqual(self.calls, [], "Missing credential cannot call remote revoke")
        self.assertFalse(result["remoteRevoked"])
        self.assertTrue(result["remoteRevocationRequired"])
        self.assertEqual(fm.list_connections(), [])

    def test_api_routes_are_local_control_only(self):
        self.assertEqual(fm.router.prefix, "/api/v1/control/food-monster")
        self.assertEqual({route.path for route in fm.router.routes}, {
            "/api/v1/control/food-monster/connections",
            "/api/v1/control/food-monster/connections/{local_id}/search",
            "/api/v1/control/food-monster/connections/{local_id}",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)

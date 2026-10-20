from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import food_monster_connector as connector
from app.services import food_monster_sync as sync
from app.services import food_monster_auto_sync as auto

ROOT = Path(__file__).resolve().parents[1]
FIRST = "a" * 32
SECOND = "b" * 32


class KB5CAutoSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.database = self.home / "test.db"
        with sqlite3.connect(self.database) as sql:
            sql.execute("PRAGMA foreign_keys=ON")
            for name in ("food_monster_connections.sql", "food_monster_knowledge_cache.sql",
                         "food_monster_sync_policy.sql"):
                sql.executescript((ROOT / "database" / name).read_text(encoding="utf-8"))
            for cid,origin,instance in (
                (FIRST,"https://gelato.vp3.me","11111111-1111-1111-1111-111111111111"),
                (SECOND,"https://another.foodmonster.example","22222222-2222-2222-2222-222222222222"),
            ):
                sql.execute(
                    "INSERT INTO food_monster_connections "
                    "(id,origin,instance_id,remote_connection_id,label,scopes_json,status) "
                    "VALUES (?,?,?,?,?,'[\"recipe\"]','active')",
                    (cid,origin,instance,"c"*32, "Gelato" if cid==FIRST else "Other restaurant"),
                )

        @contextlib.contextmanager
        def test_db():
            connection = sqlite3.connect(self.database)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        self.calls = []
        self.should_fail = False
        self.before_manifest = None
        def remote(origin, payload=None, token=None):
            self.calls.append((origin,payload,token))
            instance = ("11111111-1111-1111-1111-111111111111"
                        if origin=="https://gelato.vp3.me" else
                        "22222222-2222-2222-2222-222222222222")
            if payload is None:
                return {"ok": True,"schema":"food-monster-vp3-v1",
                        "instanceId":instance,"capabilities":["knowledge.search","knowledge.manifest"]}
            if payload["action"] == "manifest":
                if self.before_manifest:
                    action = self.before_manifest
                    self.before_manifest = None
                    action()
                if self.should_fail:
                    raise connector.FoodMonsterLinkError("Offline",502)
                sample = {"ingredients":["Flour","Water"]}
                revision = hashlib.sha256(json.dumps(sample,sort_keys=True).encode()).hexdigest()
                doc = {"id":"gelato:recipe:r1","type":"recipe","title":"House Dough",
                       "revision":revision,"content":sample,
                       "source":{"app":"food_monster","kind":"active_recipe","id":"r1"}}
                return {"ok":True,"schema":"food-monster-knowledge-manifest-v1",
                        "collection":"recipe","readOnly":True,"afterId":payload["afterId"],
                        "count":1,"complete":True,"nextAfterId":None,"results":[doc]}
            if payload["action"]=="revoke":
                return {"ok":True,"schema":"food-monster-vp3-v1","instanceId":instance,
                        "connectionId":"c"*32,"revoked":True}
            raise AssertionError("Unexpected action")

        for module in (auto,sync,connector):
            p = patch.object(module,"db",test_db)
            p.start();self.addCleanup(p.stop)
        p=patch.object(connector,"settings",SimpleNamespace(data_dir=self.home))
        p.start();self.addCleanup(p.stop)
        p=patch.object(connector,"_request",remote)
        p.start();self.addCleanup(p.stop)
        connector._vault_write({FIRST:"fm2_"+"A"*43,SECOND:"fm2_"+"B"*43})
        self.remote = remote

    def clock(self):
        return datetime.now(timezone.utc).replace(microsecond=0)

    def policy_rows(self):
        with sqlite3.connect(self.database) as sql:
            sql.row_factory=sqlite3.Row
            return [dict(x) for x in sql.execute("SELECT * FROM food_monster_sync_policy ORDER BY connection_id")]

    def docs(self):
        with sqlite3.connect(self.database) as sql:
            return sql.execute("SELECT COUNT(*) FROM food_monster_documents").fetchone()[0]

    def test_disabled_by_default_and_no_network(self):
        data=auto.get_policy(FIRST)
        self.assertFalse(data["enabled"])
        self.assertEqual(data["intervalSeconds"],21600)
        self.assertEqual(auto.run_due(now=self.clock())["claimed"],0)
        self.assertEqual(self.calls,[])

    def test_opt_in_schedules_and_completes_one_due_run(self):
        data=auto.set_policy(FIRST,True,3600)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["intervalSeconds"],3600)
        actual=auto.run_due()
        self.assertEqual(actual,{"claimed":1,"succeeded":1,"failed":0})
        self.assertEqual(self.docs(),1)
        updated=auto.get_policy(FIRST)
        self.assertEqual(updated["lastOutcome"],"synced")
        self.assertTrue(updated["lastSuccessAt"])
        self.assertFalse(updated["inProgress"])
        self.assertEqual(auto.run_due()["claimed"],0)
        self.assertTrue(all(origin=="https://gelato.vp3.me" for origin,_,_ in self.calls))

    def test_duplicate_lease_claim_prevention_and_restart_recovery(self):
        auto.set_policy(FIRST,True,3600)
        first=auto._claim(FIRST)
        self.assertRegex(first or "",r"^[0-9a-f]{32}$")
        self.assertIsNone(auto._claim(FIRST))
        self.assertEqual(auto.run_due()["claimed"],0)
        with sqlite3.connect(self.database) as sql:
            sql.execute("UPDATE food_monster_sync_policy SET lease_until=? WHERE connection_id=?",
                        (auto._stamp(self.clock()-timedelta(seconds=10)),FIRST))
        successor=auto._claim(FIRST)
        self.assertNotEqual(first,successor)
        with self.assertRaises(sync.FoodMonsterSyncCancelled):
            auto.assert_lease(FIRST,first)
        auto._finish(FIRST,first,True)
        self.assertEqual(self.policy_rows()[0]["lease_id"],successor)
        auto._finish(FIRST,successor,True)
        self.assertIsNone(self.policy_rows()[0]["lease_id"])

    def test_opt_out_invalidates_in_flight_lease_and_prevents_commits(self):
        auto.set_policy(FIRST,True,21600)
        self.before_manifest=lambda: auto.set_policy(FIRST,False,21600)
        result=auto.run_due()
        self.assertEqual(result["claimed"],1)
        self.assertEqual(result["succeeded"],0)
        self.assertEqual(result["failed"],1)
        self.assertEqual(self.docs(),0)
        self.assertFalse(auto.get_policy(FIRST)["enabled"])
        self.assertEqual(auto.run_due()["claimed"],0)

    def test_independent_restaurants_and_failure_backoff(self):
        auto.set_policy(FIRST,True,3600)
        auto.set_policy(SECOND,True,86400)
        self.should_fail=True
        result=auto.run_due()
        self.assertEqual(result["claimed"],2)
        self.assertEqual(result["failed"],2)
        self.assertEqual(self.docs(),0)
        for row in self.policy_rows():
            self.assertEqual(row["failure_count"],1)
            self.assertEqual(row["last_outcome"],"retry_backoff")
        self.should_fail=False
        # Only first restaurant becomes due; the other remains in backoff.
        with sqlite3.connect(self.database) as sql:
            sql.execute("UPDATE food_monster_sync_policy SET next_due_at=? WHERE connection_id=?",
                        (auto._stamp(self.clock()),FIRST))
        result=auto.run_due()
        self.assertEqual(result["claimed"],1)
        self.assertEqual(result["succeeded"],1)
        self.assertEqual(self.docs(),1)
        self.assertEqual(auto.get_policy(SECOND)["consecutiveFailures"],1)

    def test_invalid_schedule_and_disabled_restaurant_rejected(self):
        for enabled,seconds in ((True,5),(True,0),(True,3601),(True,86401),
                                ("true",3600),(True,True)):
            with self.subTest(enabled=enabled,seconds=seconds):
                with self.assertRaises(connector.FoodMonsterLinkError):
                    auto.set_policy(FIRST,enabled,seconds)
        with sqlite3.connect(self.database) as sql:
            sql.execute("UPDATE food_monster_connections SET status='disconnected' WHERE id=?",(SECOND,))
        with self.assertRaises(connector.FoodMonsterLinkError):
            auto.set_policy(SECOND,True,3600)

    def test_disabled_policy_stays_disabled_after_restart_and_is_persistent(self):
        auto.set_policy(FIRST,True,21600)
        auto.set_policy(FIRST,False,21600)
        self.assertEqual(self.policy_rows()[0]["enabled"],0)
        self.assertIsNone(self.policy_rows()[0]["next_due_at"])
        self.assertIsNone(self.policy_rows()[0]["lease_id"])
        new_worker=auto.FoodMonsterAutoScheduler()
        new_worker.start()
        new_worker.start()
        first_thread=new_worker._thread
        self.assertIs(first_thread,new_worker._thread)
        new_worker.stop()
        self.assertEqual(auto.run_due()["claimed"],0)

    def test_no_schedule_after_disconnection_and_private_index_removed(self):
        auto.set_policy(FIRST,True,3600)
        auto.run_due()
        result=connector.disconnect_instance(FIRST)
        self.assertTrue(result["disconnected"])
        self.assertEqual(self.docs(),0)
        self.assertFalse(any(x["connection_id"]==FIRST for x in self.policy_rows()))
        self.assertEqual(auto.run_due()["claimed"],0)
        with self.assertRaises(connector.FoodMonsterLinkError):
            auto.get_policy(FIRST)

    def test_owner_routes_are_not_paired_app_permissions(self):
        paths={r.path for r in connector.router.routes}
        self.assertIn("/api/v1/control/food-monster/connections/{local_id}/auto-sync", paths)
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("food_monster_auto_sync.scheduler.start()",main)
        self.assertIn("food_monster_auto_sync.scheduler.stop()",main)


def inspect_source(obj):
    import inspect
    return inspect.getsource(obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import food_monster_connector as connector
from app.services import food_monster_sync as sync

ROOT = Path(__file__).resolve().parents[1]


class FoodMonsterSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.database = self.path / "private-test.db"
        self.first = "a" * 32
        self.second = "b" * 32
        self.instances = {
            self.first: "11111111-1111-1111-1111-111111111111",
            self.second: "22222222-2222-2222-2222-222222222222",
        }
        self.origins = {
            self.first: "https://gelato.vp3.me",
            self.second: "https://another.restaurant.example",
        }
        with sqlite3.connect(self.database) as connection:
            connection.executescript((ROOT / "database/food_monster_connections.sql").read_text())
            connection.executescript((ROOT / "database/food_monster_knowledge_cache.sql").read_text())
            for cid in (self.first,self.second):
                connection.execute(
                    """INSERT INTO food_monster_connections
                    (id,origin,instance_id,remote_connection_id,label,scopes_json,status)
                    VALUES (?,?,?,?,?,?,'active')""",
                    (cid,self.origins[cid],self.instances[cid],"c"*32,
                     "Gelato" if cid==self.first else "Second restaurant",
                     json.dumps(["menu","recipe"] if cid==self.first else ["recipe"])),
                )
        @contextlib.contextmanager
        def testing_db():
            conn=sqlite3.connect(self.database)
            conn.row_factory=sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        self.tokens = {self.first:"fm2_"+"A"*43,self.second:"fm2_"+"B"*43}
        self.records = {
            self.first: {
                "menu": [self.doc("menu", "1", "Margherita Pizza", {"allergens":["Milk"]})],
                "recipe": [self.doc("recipe",f"r{i}",f"House Dough {i}",{"ingredients":[f"Flour {i}","Water"],"instructions":["Mix dough"]}) for i in range(1,23)]
            },
            self.second: {
                "recipe":[self.doc("recipe","r1","Second Restaurant Dough",{"ingredients":["Durum flour"]})]
            },
        }
        self.calls = []
        self.failure = None

        def fake_request(origin,payload=None,token=None):
            self.calls.append((origin,payload,token))
            cid=next((c for c,v in self.origins.items() if v==origin),None)
            if cid is None:
                raise AssertionError("Unexpected origin")
            if payload is None:
                return {"ok":True,"schema":"food-monster-vp3-v1",
                        "instanceId":self.instances[cid],
                        "capabilities":["knowledge.search","knowledge.manifest"]}
            if payload["action"]=="revoke":
                return {"ok":True,"schema":"food-monster-vp3-v1",
                        "instanceId":self.instances[cid],"connectionId":"c"*32,"revoked":True}
            if payload["action"]=="manifest":
                if token!=self.tokens[cid]:
                    raise connector.FoodMonsterLinkError("Unauthorized",403)
                kind,after = payload["type"],payload["afterId"]
                if self.failure and self.failure(cid,kind,after):
                    raise connector.FoodMonsterLinkError("Network offline",502)
                source=self.records[cid][kind]
                remaining=[item for item in source if int(item["row_id"])>after]
                page=remaining[:20]
                more=len(remaining)>20
                return {
                    "ok":True,"schema":"food-monster-knowledge-manifest-v1",
                    "collection":kind,"readOnly":True,
                    "afterId":after,
                    "results":[{k:v for k,v in item.items() if k!="row_id"} for item in page],
                    "count":len(page),"complete":not more,
                    "nextAfterId":int(page[-1]["row_id"]) if more else None,
                }
            raise AssertionError("Unknown manifest action")

        self.remote=fake_request
        for module in (connector,sync):
            q=patch.object(module,"db",testing_db);q.start();self.addCleanup(q.stop)
        q=patch.object(connector,"settings",SimpleNamespace(data_dir=self.path));q.start();self.addCleanup(q.stop)
        q=patch.object(connector,"_request",fake_request);q.start();self.addCleanup(q.stop)
        connector._vault_write(dict(self.tokens))

    @staticmethod
    def doc(kind, source, title, body):
        numeric=int(source.lstrip("r"))
        raw=json.dumps(body,sort_keys=True).encode()
        return {
            "row_id":numeric,
            "id":"gelato:"+kind+":"+source,
            "type":kind,"title":title,"revision":hashlib.sha256(raw).hexdigest(),
            "source":{"app":"food_monster","kind":"active_menu_item" if kind=="menu" else "active_recipe","id":source},
            "content":body,"updatedAt":"2026-10-20 10:00:00",
        }

    def rows(self,table,connection=None):
        with sqlite3.connect(self.database) as db:
            db.row_factory=sqlite3.Row
            if connection:
                return [dict(r) for r in db.execute(
                    "SELECT * FROM "+table+" WHERE connection_id=?",(connection,))]
            return [dict(r) for r in db.execute("SELECT * FROM "+table)]

    def test_full_paged_sync_is_isolated_and_indexed(self):
        a=sync.synchronize(self.first)
        self.assertEqual(a["status"],"synced")
        self.assertEqual(a["collections"]["recipe"]["created"],22)
        self.assertEqual(a["collections"]["menu"]["created"],1)
        self.assertEqual(len(self.rows("food_monster_documents",self.first)),23)
        self.assertEqual(len(self.rows("food_monster_documents",self.second)),0)
        self.assertEqual(len(self.rows("food_monster_documents_fts")),23)
        pages=[payload for origin,payload,_ in self.calls if payload and payload.get("action")=="manifest"]
        self.assertEqual([x["afterId"] for x in pages if x["type"]=="recipe"],[0,20])
        search=sync.query_cache(self.first,"flour 8","recipe")
        self.assertFalse(search["liveVerified"])
        self.assertEqual(search["freshness"],"unverified_cached_snapshot")
        self.assertTrue(any(r["id"]=="gelato:recipe:r8" for r in search["results"]))
        self.assertEqual(search["origin"],self.origins[self.first])
        self.assertNotIn("fm2_",json.dumps(search))
        status=sync.status(self.first)
        self.assertEqual(status["categories"]["recipe"]["documents"],22)
        self.assertEqual(status["categories"]["recipe"]["completeGenerations"],1)

    def test_revision_changes_update_exact_record(self):
        sync.synchronize(self.first)
        old=next(r for r in self.rows("food_monster_documents",self.first) if r["source_id"]=="gelato:recipe:r1")
        item=self.records[self.first]["recipe"][0]
        item["content"]={"ingredients":["Sourdough flour"],"instructions":["Cold proof"]}
        item["revision"]=hashlib.sha256(json.dumps(item["content"],sort_keys=True).encode()).hexdigest()
        response=sync.synchronize(self.first)
        self.assertEqual(response["collections"]["recipe"]["updated"],1)
        new=next(r for r in self.rows("food_monster_documents",self.first) if r["source_id"]=="gelato:recipe:r1")
        self.assertEqual(new["id"],old["id"])
        self.assertNotEqual(new["revision"],old["revision"])
        self.assertEqual(len(self.rows("food_monster_documents_fts")),23)
        found=sync.query_cache(self.first,"Sourdough","recipe")
        self.assertEqual([x["id"] for x in found["results"]],["gelato:recipe:r1"])

    def test_two_complete_absences_are_required_to_delete(self):
        sync.synchronize(self.first)
        self.records[self.first]["recipe"]=[x for x in self.records[self.first]["recipe"] if x["id"]!="gelato:recipe:r2"]
        first=sync.synchronize(self.first)
        self.assertEqual(first["collections"]["recipe"]["pendingRemoval"],1)
        self.assertEqual(first["collections"]["recipe"]["removed"],0)
        self.assertEqual(len(self.rows("food_monster_documents",self.first)),23)
        second=sync.synchronize(self.first)
        self.assertEqual(second["collections"]["recipe"]["removed"],1)
        self.assertEqual(len(self.rows("food_monster_documents",self.first)),22)
        self.assertEqual(len(self.rows("food_monster_documents_fts")),22)
        self.assertFalse(sync.query_cache(self.first,"House Dough 2","recipe")["results"])

    def test_incomplete_sweep_never_tombstones_or_commits_partial_results(self):
        sync.synchronize(self.first)
        original=self.rows("food_monster_documents",self.first)
        self.records[self.first]["recipe"]=[
            x for x in self.records[self.first]["recipe"] if x["id"]!="gelato:recipe:r3"
        ]
        self.records[self.first]["recipe"][0]["title"]="Changed but incomplete"
        self.failure=lambda cid,kind,after: cid==self.first and kind=="recipe" and after==20
        with self.assertRaises(connector.FoodMonsterLinkError):
            sync.synchronize(self.first)
        current=self.rows("food_monster_documents",self.first)
        self.assertEqual([(x["id"],x["revision"],x["missing_complete_sweeps"]) for x in current],
                         [(x["id"],x["revision"],x["missing_complete_sweeps"]) for x in original])
        self.assertTrue(sync.status(self.first)["categories"]["recipe"]["lastError"])
        self.failure=None
        one=sync.synchronize(self.first)
        self.assertEqual(one["collections"]["recipe"]["pendingRemoval"],1)

    def test_bogus_manifest_identity_or_page_fails_without_writes(self):
        with patch.object(sync,"_pages",side_effect=connector.FoodMonsterLinkError("Wrong cursor",502)):
            with self.assertRaises(connector.FoodMonsterLinkError):
                sync.synchronize(self.first)
        self.assertEqual(self.rows("food_monster_documents"),[])

    def test_bad_document_provenance_rejected(self):
        for change in (
            {"revision":"not-sha"},
            {"type":"menu"},
            {"source":{"app":"bad","kind":"active_recipe","id":"r1"}},
            {"content":{"blob":"Z"*40000}},
        ):
            sample=self.doc("recipe","r1","House Dough",{"ingredients":["Flour"]})
            sample.update(change)
            with self.subTest(change=list(change)):
                with self.assertRaises(connector.FoodMonsterLinkError):
                    sync._validate_document(sample,"recipe")

    def test_disconnect_purges_private_cache_and_fts_without_touching_other_restaurant(self):
        sync.synchronize(self.first)
        sync.synchronize(self.second)
        self.assertEqual(len(self.rows("food_monster_documents")),24)
        result=connector.disconnect_instance(self.first)
        self.assertTrue(result["remoteRevoked"])
        self.assertEqual(self.rows("food_monster_documents",self.first),[])
        self.assertEqual(len(self.rows("food_monster_documents_fts")),1)
        self.assertEqual(len(self.rows("food_monster_sync_state",self.first)),0)
        self.assertEqual(sync.query_cache(self.second,"Durum","recipe")["count"],1)
        with self.assertRaises(connector.FoodMonsterLinkError):
            sync.query_cache(self.first,"Dough")

    def test_source_revocation_destroys_credentials_and_all_local_documents(self):
        sync.synchronize(self.first)
        original=self.remote
        def revoked(origin,payload=None,token=None):
            if payload and payload.get("action")=="manifest":
                raise connector.FoodMonsterLinkError("Owner revoked",403)
            return original(origin,payload,token)
        with self.assertRaises(connector.FoodMonsterLinkError):
            sync.synchronize(self.first,fetch=revoked)
        self.assertEqual(self.rows("food_monster_documents",self.first),[])
        self.assertEqual(self.rows("food_monster_documents_fts"),[])
        self.assertNotIn(self.first,connector._vault_read())
        with self.assertRaises(connector.FoodMonsterLinkError):
            sync.status(self.first)
        self.assertEqual(connector.list_connections()[0]["status"],"disconnected")

    def test_scope_denial_on_cached_records(self):
        sync.synchronize(self.first)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE food_monster_connections SET scopes_json='[\"menu\"]' WHERE id=?",(self.first,))
        with self.assertRaises(connector.FoodMonsterLinkError):
            sync.query_cache(self.first,"dough","recipe")

    def test_stale_references_do_not_go_into_shared_knowledge_index(self):
        schema=(ROOT/"database/food_monster_knowledge_cache.sql").read_text()
        self.assertNotIn("INSERT INTO knowledge_items",schema)
        self.assertNotIn("knowledge_chunks",schema)
        paths=[r.path for r in connector.router.routes]
        self.assertIn("/api/v1/control/food-monster/connections/{local_id}/sync",paths)
        self.assertIn("/api/v1/control/food-monster/connections/{local_id}/sync-status",paths)
        self.assertIn("/api/v1/control/food-monster/connections/{local_id}/cache/search",paths)


if __name__=="__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from app.services import food_monster_chat as chat
from app.services.food_monster_connector import FoodMonsterLinkError

ROOT = Path(__file__).resolve().parents[1]
REV = "a" * 64

def instance(label: str, origin: str, id_: str, *, scopes=None) -> dict:
    return {"id": id_, "label": label, "origin": origin,
            "instanceId": "11111111-1111-1111-1111-111111111111" if id_ == "1" else "22222222-2222-2222-2222-222222222222",
            "status": "active", "scopes": scopes or ["menu", "recipe"]}

G = instance("Gelato", "https://gelato.vp3.me", "1")
P = instance("Pizza East", "https://pizza-east.example.com", "2")

def result(conn: dict, kind: str) -> dict:
    return {"origin": conn["origin"], "instanceId": conn["instanceId"],
            "results": [{"id": f"gelato:{kind}:house-dough", "type": kind,
                         "title": "House Dough", "revision": REV,
                         "content": {"ingredients": ["Flour", "Water"], "instructions": ["Mix dough"]}}]}


class OwnerChatTests(unittest.TestCase):
    def setUp(self):
        self.calls: list[tuple[str, str, str, int]] = []

    def searcher(self, conn_id, q, kind, limit):
        self.calls.append((conn_id, q, kind, limit))
        conn = G if conn_id == "1" else P
        return result(conn, kind if kind != "all" else "menu")

    def packet(self, text, items=None, active=True, search=None):
        return chat.owner_packet(text, allow_knowledge=active,
                                 connected=lambda: list([G] if items is None else items),
                                 search=search or self.searcher)

    def test_irrelevant_chat_has_zero_remote_calls(self):
        packet = self.packet("Set a reminder for tomorrow")
        self.assertEqual(self.calls, [])
        self.assertFalse(packet["queried"])
        self.assertEqual(packet["sources"], [])

    def test_no_knowledge_permission_no_remote_calls(self):
        packet = self.packet("What's the dough recipe at Gelato?", active=False)
        self.assertEqual(self.calls, [])
        self.assertFalse(packet["queried"])

    def test_owner_automatically_gets_named_dough(self):
        packet = self.packet("What's our current dough recipe at Gelato?")
        self.assertEqual(self.calls, [("1", "dough", "recipe", 3)])
        self.assertEqual(len(packet["sources"]), 1)
        self.assertIn("gelato.vp3.me", packet["context"])
        self.assertIn("House Dough", chat.citations_footer(packet["sources"]))
        self.assertNotIn("fm2_", str(packet))
        self.assertNotIn("fmlink_", str(packet))

    def test_multi_restaurant_requires_source_when_ambiguous(self):
        packet = self.packet("What is our dough recipe?", items=[G, P])
        self.assertFalse(packet["queried"])
        self.assertIn("Name the restaurant", packet["context"])
        self.assertEqual(self.calls, [])

    def test_only_explicitly_named_source_is_queried(self):
        packet = self.packet("What is the dough recipe at Gelato?", items=[G, P])
        self.assertEqual([call[0] for call in self.calls], ["1"])
        self.assertEqual(packet["sources"][0]["origin"], G["origin"])

    def test_compare_fetches_separate_sources(self):
        packet = self.packet("Compare dough recipes across all my restaurants", items=[G, P])
        self.assertEqual([call[0] for call in self.calls], ["1", "2"])
        self.assertEqual(len(packet["sources"]), 2)
        self.assertNotEqual(packet["sources"][0]["instanceId"],packet["sources"][1]["instanceId"])

    def test_menu_reads_remain_menu(self):
        packet = self.packet("Which allergens does our pepperoni pizza contain at Gelato?")
        self.assertEqual(self.calls, [("1", "pepperoni", "menu", 3)])
        self.assertEqual(packet["sources"][0]["type"], "menu")

    def test_scopes_prevent_remote_recipe_query(self):
        packet = self.packet("What's the dough recipe at Gelato?",
                             items=[instance("Gelato",G["origin"],"1",scopes=["menu"])])
        self.assertEqual(self.calls, [])
        self.assertIn("not authorized", packet["context"])

    def test_closed_connection_never_queried(self):
        closed = dict(G, status="disconnected")
        packet = self.packet("What's the dough recipe at Gelato?", items=[closed])
        self.assertEqual(self.calls, [])
        self.assertIn("No authorized", packet["context"])

    def test_unavailable_source_fails_closed(self):
        def failed(*_):
            raise FoodMonsterLinkError("Source offline", 502)
        packet = self.packet("What's the dough recipe at Gelato?", search=failed)
        self.assertEqual(packet["sources"], [])
        self.assertIn("unavailable", packet["context"])
        self.assertNotIn("Source offline", packet["context"])

    def test_mismatched_instance_identity_is_rejected(self):
        def mismatched(*_):
            packet = result(G, "recipe")
            packet["instanceId"] = "00000000-0000-0000-0000-000000000000"
            return packet
        packet = self.packet("What's the dough recipe at Gelato?", search=mismatched)
        self.assertEqual(packet["sources"], [])
        self.assertIn("unavailable", packet["context"])

    def test_out_of_scope_record_rejected(self):
        def out_of_scope(*_):
            return result(G, "menu")
        packet = self.packet("What's the dough recipe at Gelato?", search=out_of_scope)
        self.assertEqual(packet["sources"], [])
        self.assertIn("did not match the requested category", packet["context"])

    def test_revision_and_source_validation(self):
        rec = result(G, "recipe")["results"][0]
        rec["revision"] = "not-a-checksum"
        packet = self.packet("What's the dough recipe at Gelato?",
                             search=lambda *args: {"origin": G["origin"],
                                 "instanceId": G["instanceId"], "results": [rec]})
        self.assertEqual(packet["sources"], [])
        self.assertIn("source validation", packet["context"])

    def test_evidence_is_bounded_and_not_raw_db_dump(self):
        def long_result(*_):
            value = result(G,"recipe")
            value["results"][0]["content"] = {"text": "x" * 100000}
            return value
        packet = self.packet("What's the dough recipe at Gelato?", search=long_result)
        self.assertLess(len(packet["context"]), 1700)
        self.assertEqual(len(packet["sources"]), 1)

    def test_no_specific_item_no_search(self):
        packet = self.packet("Can you show me the recipes at Gelato?")
        self.assertEqual(self.calls, [])
        self.assertIn("Specify",packet["context"])

    def test_citations_are_unique_and_do_not_include_credentials(self):
        src = {"origin": G["origin"], "id":"gelato:recipe:1",
               "label":"Gelato","title":"House Dough"}
        footer = chat.citations_footer([src,src])
        self.assertEqual(footer.count("- Food Monster /"),1)
        self.assertEqual(chat.citations_footer([]),"")

    def test_owner_chat_gate_and_metadata_are_present(self):
        source = (ROOT / "app" / "services" / "context_chat.py").read_text(encoding="utf-8")
        self.assertIn('source_app_key == "owner" and owner_tools and include_knowledge',source)
        self.assertIn('not (r6_active or r6_selected_off or current_settings.get("agent_eyes_local_only"))', source)
        self.assertIn('food_monster_chat.owner_packet(text, allow_knowledge=True)',source)
        self.assertIn('food_monster_chat.citations_footer(food_monster_packet["sources"])',source)
        self.assertIn('"food_monster_sources": food_monster_packet["sources"]',source)
        self.assertNotIn('accessToken',inspect.getsource(chat.owner_packet))


if __name__ == "__main__":
    unittest.main(verbosity=2)

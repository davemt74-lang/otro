"""Section 8: owner Agent context for completed transcripts and finalized meeting cards."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import db, initialize_database
from app.services import canonical_context, interactive_agent_context, local_transcription_sessions as tx


class InteractiveAgentContextSection8(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="interactive-context-section8-")
        previous = settings.data_dir
        object.__setattr__(settings, "data_dir", Path(self.temp.name))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(object.__setattr__, settings, "data_dir", previous)
        initialize_database()
        with db() as connection:
            self.agent_id = int(connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()[0])

    def _completed_transcript(self, title: str, text: str) -> str:
        session = tx.start(title)["session"]["id"]
        tx.append(session, text, "a" * 32, 100)
        tx.stop(session)
        return session

    def _meeting_card(self, title: str, summary: str) -> int:
        with db() as connection:
            conversation_id = "section8" + str(connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] + 1)
            connection.execute(
                "INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
                (conversation_id, self.agent_id, "owner", "Meeting fixture"),
            )
            card = {
                "card_type": "meeting",
                "version": "v0.40",
                "meeting_id": "meeting-section8",
                "title": title,
                "status": "completed",
                "summary": summary,
                "key_points": [{"text": "Orion launch remains Friday."}],
                "decisions": [{"decision": "Ship the Orion launch Friday."}],
                "actions": [],
            }
            cursor = connection.execute(
                """
                INSERT INTO conversation_messages(conversation_id,role,content,source_app_key,model,metadata_json)
                VALUES(?,'assistant',?,'owner','vp3-meeting-intelligence',?)
                """,
                (conversation_id, summary, json.dumps(card)),
            )
            return int(cursor.lastrowid)

    def _canonical(self, query: str, *, owner: bool = True, include_knowledge: bool = True):
        source = "owner" if owner else "app:vp3"
        permissions = set() if owner else {"agent.chat", "knowledge.search"}
        return canonical_context.build_authorized_context(
            agent_id=self.agent_id,
            query=query,
            source_app_key=source,
            permissions=permissions,
            owner=owner,
            include_memory=False,
            include_knowledge=include_knowledge,
            include_contacts=False,
            settings={
                "include_memory": False,
                "include_knowledge": include_knowledge,
                "include_contacts": False,
                "include_agent_eyes": False,
                "agent_eyes_local_only": False,
                "cloud_allowed": True,
                "max_context_chars": 12000,
            },
            max_context_chars=12000,
            cloud_allowed=True,
            include_collaboration=False,
        )

    def test_completed_transcript_enters_owner_context_with_unverified_speaker_label(self):
        session = self._completed_transcript("Orion notes", "We decided the Orion launch remains Friday.")
        context = self._canonical("What did we decide about the Orion launch?")
        self.assertIn("Orion launch remains Friday", context.interactive["fragment"])
        self.assertIn("speaker identity unverified", context.interactive["fragment"])
        self.assertTrue(any(ref.get("id") == session for ref in context.interactive["sources"]))

    def test_active_transcript_never_enters_context(self):
        active = tx.start("Private active")["session"]["id"]
        tx.append(active, "active-secret-marker", "b" * 32, 0)
        result = interactive_agent_context.collect_owner("active-secret-marker")
        self.assertNotIn("active-secret-marker", result["fragment"])
        tx.stop(active)

    def test_knowledge_opt_out_removes_interactive_context(self):
        self._completed_transcript("Opt out", "opt-out-secret-marker")
        context = self._canonical("opt-out-secret-marker", include_knowledge=False)
        self.assertEqual(context.interactive["fragment"], "")
        self.assertFalse(any(ref.get("kind") == "local_transcription" for ref in context.source_refs))

    def test_paired_app_never_gets_private_local_transcript(self):
        self._completed_transcript("Paired boundary", "paired-private-marker")
        context = self._canonical("paired-private-marker", owner=False, include_knowledge=True)
        self.assertEqual(context.interactive["fragment"], "")
        self.assertNotIn("paired-private-marker", canonical_context.system_prompt({"name": "Agent", "instructions": ""}, context))

    def test_deleted_transcript_disappears_immediately(self):
        session = self._completed_transcript("Delete me", "deletion-marker-section8")
        self.assertIn("deletion-marker-section8", interactive_agent_context.collect_owner("deletion-marker-section8")["fragment"])
        tx.delete(session)
        self.assertNotIn("deletion-marker-section8", interactive_agent_context.collect_owner("deletion-marker-section8")["fragment"])

    def test_finalized_meeting_card_is_retrievable_without_raw_transcript(self):
        message_id = self._meeting_card("Orion review", "Reviewed summary says Orion ships Friday.")
        context = self._canonical("What did the Orion meeting decide?")
        self.assertIn("Reviewed summary says Orion ships Friday", context.interactive["fragment"])
        self.assertTrue(any(ref.get("kind") == "meeting_summary" and ref.get("id") == message_id for ref in context.source_refs))
        self.assertNotIn("TRANSCRIPT DATA", context.interactive["fragment"])

    def test_interactive_fragment_is_bounded(self):
        self._completed_transcript("Bounded", "needle " + ("x" * 7000))
        result = interactive_agent_context.collect_owner("needle transcript", max_chars=500)
        self.assertLessEqual(len(result["fragment"]), 500)

    def test_provenance_supports_string_transcript_ids(self):
        self._completed_transcript("Provenance", "provenance-marker")
        context = self._canonical("provenance-marker")
        entry = next(item for item in context.provenance if item.get("layer") == "local_transcription")
        self.assertTrue(entry.get("resource_key"))
        self.assertEqual(entry.get("resource_id"), 0)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(InteractiveAgentContextSection8)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"INTERACTIVE_AGENT_CONTEXT_SECTION8=PASS ({result.testsRun} cases)")

"""HomeServer telephone ingress must never bypass Agent grants or actions."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Isolate the phone transport contract from full HomeServer knowledge parsing,
# model providers and optional desktop dependencies. The existing HomeServer
# integration workflows exercise the real canonical runtime separately.
_chat_stub = types.ModuleType("app.services.context_chat")
_chat_stub.chat = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not mocked"))
with patch.dict(sys.modules, {"app.services.context_chat": _chat_stub}):
    from app import phone_api
from telephony.security import signed_envelope


class PhoneAdapterTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("""CREATE TABLE agent_phone_receipts (
            event_id TEXT PRIMARY KEY,agent_id INTEGER,state TEXT,response_json TEXT)""")

        @contextmanager
        def fake_db():
            yield self.conn
            self.conn.commit()

        self.db_patch = patch.object(phone_api, "db", fake_db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.env_patch = patch.dict(os.environ, {"VP3_PHONE_SERVICE_SECRET": "test-secret"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        app = FastAPI()
        app.include_router(phone_api.router)
        self.client = TestClient(app)
        self.event = {"event_id": "SM_test01", "channel": "sms", "agent_id": 9,
                      "message": "Do you have hours?", "caller_verified": False, "allow_actions": False}

    def post(self, *, signed=True):
        raw = json.dumps(self.event, separators=(",", ":")).encode()
        stamp = int(time.time())
        headers = {"Content-Type": "application/json", "X-VP3-Timestamp": str(stamp),
                   "X-VP3-Signature": signed_envelope("test-secret", stamp, raw) if signed else "forgery"}
        return self.client.post("/api/v1/phone/agent", content=raw, headers=headers)

    def test_replay_returns_original_without_second_agent_turn(self):
        calls = []

        def chat(source, message, conversation_id, **kwargs):
            calls.append((source, message, conversation_id, kwargs))
            return {"reply": "We are open", "conversation_id": "conv_created"}

        with patch.object(phone_api.agent_routing, "resolve_agent", return_value={"id": 9, "is_primary": False}), \
             patch.object(phone_api.context_chat, "chat", side_effect=chat):
            self.assertEqual(self.post().status_code, 200)
            replay = self.post()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["reply"], "We are open")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "app:phone-gateway")
        self.assertTrue(calls[0][3]["read_only"])
        self.assertEqual(calls[0][3]["tool_permissions"], set())
        self.assertFalse(calls[0][3]["include_memory"])

    def test_forged_phone_event_never_invokes_agent(self):
        with patch.object(phone_api.context_chat, "chat") as chat:
            self.assertEqual(self.post(signed=False).status_code, 401)
            chat.assert_not_called()

    def test_private_owner_agent_is_rejected(self):
        with patch.object(phone_api.agent_routing, "resolve_agent", return_value={"id": 9, "is_primary": True}), \
             patch.object(phone_api.context_chat, "chat") as chat:
            self.assertEqual(self.post().status_code, 403)
            chat.assert_not_called()

    def test_injected_action_authorization_is_rejected(self):
        self.event["allow_actions"] = True
        with patch.object(phone_api.context_chat, "chat") as chat:
            self.assertEqual(self.post().status_code, 403)
            chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Real SDK smoke test: LiveKit's LLM slot delegates to canonical Main Chat."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

try:
    from livekit.agents import llm
    from telephony.livekit_agent import VP3CanonicalChatLLM, build_server, create_speech_session
except ImportError:
    llm = None

from test_telephony_livekit_voice import settings
from telephony.livekit_voice import SerializedMainChat
from telephony.voice_turns import VoiceTurnError


@unittest.skipIf(llm is None, "LiveKit worker dependency is optional")
class LiveKitSDKTests(unittest.TestCase):
    def test_worker_registration_does_not_need_a_second_agent_or_model(self):
        self.assertEqual(type(build_server(settings())).__name__, "AgentServer")

    def test_canonical_llm_slot_is_not_a_second_model(self):
        calls = []
        class FakeMainChat:
            def turn(self, text):
                calls.append(text)
                return "Canonical answer"

        async def scenario():
            model = VP3CanonicalChatLLM(SerializedMainChat(FakeMainChat()))
            self.assertEqual(model.provider, "vp3-homeserver")
            self.assertEqual(model.model, "canonical-context-chat")
            context = llm.ChatContext()
            context.add_message(role="user", content="What is on my list?")
            chunks = []
            async with model.chat(chat_ctx=context) as stream:
                async for chunk in stream:
                    if chunk.delta and chunk.delta.content:
                        chunks.append(chunk.delta.content)
            async with model.chat(chat_ctx=context) as stream:
                async for chunk in stream:
                    self.fail(f"Repeated transcript generated another Chat response: {chunk}")
            context.add_message(role="user", content="What about tomorrow?")
            async with model.chat(chat_ctx=context) as stream:
                async for chunk in stream:
                    if chunk.delta and chunk.delta.content:
                        chunks.append(chunk.delta.content)
            with self.assertRaises(VoiceTurnError):
                model.chat(chat_ctx=context, tools=[object()])
            await model.aclose()
            return chunks

        self.assertEqual(asyncio.run(scenario()), ["Canonical answer", "Canonical answer"])
        self.assertEqual(calls, ["What is on my list?", "What about tomorrow?"])

    def test_speech_session_accepts_only_stt_tts_vad_and_canonical_chat(self):
        class FakeMainChat:
            def turn(self, text):
                return "Okay"
        async def scenario():
            with patch.dict(os.environ, {
                "LIVEKIT_API_KEY": "test-only-no-network",
                "LIVEKIT_API_SECRET": "test-only-no-network",
            }):
                session = create_speech_session(settings(), SerializedMainChat(FakeMainChat()))
                self.assertIsInstance(session.llm, VP3CanonicalChatLLM)
                self.assertIsNotNone(session.stt)
                self.assertIsNotNone(session.tts)
                self.assertIsNotNone(session.vad)
                await session.aclose()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

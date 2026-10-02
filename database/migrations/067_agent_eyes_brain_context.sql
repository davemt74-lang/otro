-- Agent Eyes context is explicitly opted into by the local owner. Once used,
-- conversation history must remain on local inference even after opt-out.
ALTER TABLE conversation_context_settings ADD COLUMN include_agent_eyes INTEGER NOT NULL DEFAULT 0 CHECK(include_agent_eyes IN (0,1));
ALTER TABLE conversation_context_settings ADD COLUMN agent_eyes_local_only INTEGER NOT NULL DEFAULT 0 CHECK(agent_eyes_local_only IN (0,1));

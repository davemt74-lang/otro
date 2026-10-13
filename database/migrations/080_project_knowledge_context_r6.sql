CREATE TABLE conversation_knowledge_context_r6 (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL,
    peer_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('all','off','project','folder')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

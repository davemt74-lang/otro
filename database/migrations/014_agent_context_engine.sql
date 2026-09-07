CREATE TABLE IF NOT EXISTS conversation_context_settings (
    conversation_id TEXT PRIMARY KEY,
    include_memory INTEGER NOT NULL DEFAULT 1 CHECK (include_memory IN (0,1)),
    include_knowledge INTEGER NOT NULL DEFAULT 1 CHECK (include_knowledge IN (0,1)),
    include_contacts INTEGER NOT NULL DEFAULT 1 CHECK (include_contacts IN (0,1)),
    cloud_allowed INTEGER NOT NULL DEFAULT 0 CHECK (cloud_allowed IN (0,1)),
    max_context_chars INTEGER NOT NULL DEFAULT 12000 CHECK (max_context_chars BETWEEN 2000 AND 24000),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_retrieval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    source_app_key TEXT NOT NULL,
    memory_count INTEGER NOT NULL DEFAULT 0,
    knowledge_count INTEGER NOT NULL DEFAULT 0,
    contact_count INTEGER NOT NULL DEFAULT 0,
    context_chars INTEGER NOT NULL DEFAULT 0,
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_context_retrieval_conversation_created
ON context_retrieval_events(conversation_id, created_at DESC);

ALTER TABLE agent_runs ADD COLUMN contact_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN context_chars INTEGER NOT NULL DEFAULT 0;

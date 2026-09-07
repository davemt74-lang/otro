CREATE TABLE IF NOT EXISTS model_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO model_providers(provider_key, kind, name, base_url, model, enabled)
VALUES ('ollama', 'ollama', 'Ollama', 'http://127.0.0.1:11434', '', 0);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    agent_id INTEGER,
    source_app_key TEXT NOT NULL DEFAULT 'owner',
    title TEXT NOT NULL DEFAULT 'New conversation',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_source_updated
ON conversations(source_app_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content TEXT NOT NULL,
    source_app_key TEXT,
    model TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation
ON conversation_messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    source_app_key TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'started' CHECK (status IN ('started','completed','failed')),
    memory_count INTEGER NOT NULL DEFAULT 0,
    knowledge_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_created_at
ON agent_runs(created_at DESC);

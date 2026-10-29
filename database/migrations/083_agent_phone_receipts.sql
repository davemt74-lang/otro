-- One inbound provider event may create only one canonical Agent turn.
CREATE TABLE IF NOT EXISTS agent_phone_receipts (
    event_id TEXT PRIMARY KEY,
    agent_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('processing','complete','needs_review')),
    response_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

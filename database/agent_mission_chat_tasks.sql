CREATE TABLE IF NOT EXISTS agent_mission_chat_tasks_v1 (
    mission_id TEXT PRIMARY KEY REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    draft_json TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A5C9: source/conversation-bound command reviews and immutable successor links.
CREATE TABLE IF NOT EXISTS agent_mission_followups_v1 (
 source_app_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 parent_agent_id INTEGER NOT NULL REFERENCES agents(id),
 objective TEXT NOT NULL,
 command_json TEXT NOT NULL,
 mission_id TEXT REFERENCES agent_missions_v1(id),
 snapshot_hash TEXT,
 review_hash TEXT,
 plan_json TEXT,
 prepared_provider TEXT,
 prepared_model TEXT,
 state TEXT NOT NULL DEFAULT 'choosing' CHECK(state IN ('choosing','proposed','applied','dismissed')),
 result_mission_id TEXT REFERENCES agent_missions_v1(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 expires_at TEXT NOT NULL DEFAULT (datetime('now','+15 minutes')),
 decided_at TEXT,
 PRIMARY KEY(source_app_key,request_id)
);
CREATE TABLE IF NOT EXISTS agent_mission_continuity_v1 (
 predecessor_id TEXT PRIMARY KEY REFERENCES agent_missions_v1(id),
 successor_id TEXT NOT NULL UNIQUE REFERENCES agent_missions_v1(id),
 source_app_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(source_app_key,request_id) REFERENCES agent_mission_followups_v1(source_app_key,request_id)
);

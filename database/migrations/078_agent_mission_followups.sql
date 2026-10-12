-- Ensure mission tables exist before copying routes on a fresh install.
-- Additive v1 mission runtime. Existing team and delegation tables remain untouched.
CREATE TABLE IF NOT EXISTS agent_missions_v1 (
    id TEXT PRIMARY KEY,
    source_app_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    parent_agent_id INTEGER NOT NULL,
    objective TEXT NOT NULL CHECK(length(objective) BETWEEN 1 AND 16000),
    status TEXT NOT NULL DEFAULT 'planned'
      CHECK(status IN ('planned','running','completed','partial','failed','cancelled','waiting_review')),
    client_request_id TEXT NOT NULL,
    max_parallel INTEGER NOT NULL DEFAULT 4 CHECK(max_parallel BETWEEN 1 AND 4),
    max_tasks INTEGER NOT NULL DEFAULT 12 CHECK(max_tasks BETWEEN 1 AND 12),
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(source_app_key, client_request_id),
    FOREIGN KEY(parent_agent_id) REFERENCES agents(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_missions_v1_source
    ON agent_missions_v1(source_app_key, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_mission_workers_v1 (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    instructions TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready'
      CHECK(status IN ('ready','running','completed','failed','cancelled','interrupted')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_workers_v1_mission ON agent_mission_workers_v1(mission_id);

CREATE TABLE IF NOT EXISTS agent_mission_tasks_v1 (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL REFERENCES agent_mission_workers_v1(id),
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued'
      CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted')),
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_id TEXT,
    result TEXT NOT NULL DEFAULT '',
    error TEXT,
    provider_key TEXT,
    model TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mission_id, position)
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_v1_status
    ON agent_mission_tasks_v1(mission_id, status, position);

CREATE TABLE IF NOT EXISTS agent_mission_events_v1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    task_id TEXT,
    kind TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_events_v1_mission
    ON agent_mission_events_v1(mission_id, id);
CREATE TABLE agent_mission_chat_routes_a5c9 (
    source_app_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    parent_agent_id INTEGER NOT NULL REFERENCES agents(id),
    objective TEXT NOT NULL,
    route TEXT NOT NULL CHECK(route IN ('chat','specialists','followup')),
    reason TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    model TEXT NOT NULL,
    mission_id TEXT REFERENCES agent_missions_v1(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key,request_id)
);
INSERT INTO agent_mission_chat_routes_a5c9 SELECT * FROM agent_mission_chat_routes_v1;
DROP TABLE agent_mission_chat_routes_v1;
ALTER TABLE agent_mission_chat_routes_a5c9 RENAME TO agent_mission_chat_routes_v1;
CREATE UNIQUE INDEX agent_mission_chat_routes_mission ON agent_mission_chat_routes_v1(mission_id) WHERE mission_id IS NOT NULL;
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

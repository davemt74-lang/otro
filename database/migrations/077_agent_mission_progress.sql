-- A5C8: observable worker activity and source-scoped, durable notices.
CREATE TABLE IF NOT EXISTS agent_mission_progress_v1 (
 task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
 lease_id TEXT NOT NULL,
 stage TEXT NOT NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_mission_notices_v1 (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id INTEGER NOT NULL UNIQUE REFERENCES agent_mission_events_v1(id) ON DELETE CASCADE,
 mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
 source_app_key TEXT NOT NULL,
 kind TEXT NOT NULL,
 title TEXT NOT NULL,
 level TEXT NOT NULL,
 notification_id INTEGER REFERENCES notifications(id) ON DELETE SET NULL,
 read_at TEXT,
 resolved_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mission_notices_source ON agent_mission_notices_v1(source_app_key,id DESC);

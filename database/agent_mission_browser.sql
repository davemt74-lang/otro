-- A5B: explicitly approved origin-scoped browser evidence per mission worker.
-- A task is never granted browser authority by a model prompt.
CREATE TABLE IF NOT EXISTS agent_mission_browser_v1 (
  task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
  mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  source_app_key TEXT NOT NULL,
  pinned_ip TEXT NOT NULL,
  approved_origin TEXT NOT NULL,
  current_url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'approved'
    CHECK(status IN ('approved','capturing','closed')),
  capture_token TEXT,
  visit_count INTEGER NOT NULL DEFAULT 0 CHECK(visit_count BETWEEN 0 AND 5),
  text_snapshot TEXT NOT NULL DEFAULT '',
  image_base64 TEXT NOT NULL DEFAULT '',
  page_title TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_mission_browser_source_v1
ON agent_mission_browser_v1(mission_id, source_app_key, status);

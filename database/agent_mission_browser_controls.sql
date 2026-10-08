-- A5B3: versioned, explicitly approved non-submitting browser UI interactions.
-- Typed field contents and user-entered selections are never persisted.
CREATE TABLE IF NOT EXISTS agent_mission_browser_controls_v3 (
  task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
  mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  session_token TEXT NOT NULL,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  pending_action_json TEXT NOT NULL DEFAULT '{}',
  action_count INTEGER NOT NULL DEFAULT 0 CHECK(action_count BETWEEN 0 AND 6),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_browser_controls_v3_mission
ON agent_mission_browser_controls_v3(mission_id);

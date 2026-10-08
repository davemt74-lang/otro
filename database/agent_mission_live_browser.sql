-- A5B2: restart-safe metadata for a bounded in-memory Chromium session.
-- Browser process state/cookies are NEVER persisted or synchronized.
CREATE TABLE IF NOT EXISTS agent_mission_live_browser_v2 (
  task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
  mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  source_app_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('starting','live','navigating','stopped')),
  session_token TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  link_candidates_json TEXT NOT NULL DEFAULT '[]',
  pending_proposal_json TEXT NOT NULL DEFAULT '{}',
  last_approved_proposal TEXT NOT NULL DEFAULT '',
  screenshot_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mission_live_browser_status_v2
ON agent_mission_live_browser_v2(mission_id,status);

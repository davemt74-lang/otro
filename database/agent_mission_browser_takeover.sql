-- A5B4: exclusive, bounded browser control lease and a safe search-submit review.
-- No browser password, form content or credentials are persisted.
CREATE TABLE IF NOT EXISTS agent_mission_browser_takeover_v4 (
  task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
  mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  source_app_key TEXT NOT NULL,
  session_token TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'agent' CHECK(mode IN ('agent','owner')),
  lease_id TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  expires_at TEXT,
  actions_used INTEGER NOT NULL DEFAULT 0 CHECK(actions_used BETWEEN 0 AND 8),
  pending_form_json TEXT NOT NULL DEFAULT '{}',
  forms_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_mission_browser_plans_v4 (
 task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
 mission_id TEXT NOT NULL,
 source_app_key TEXT NOT NULL,
 session_token TEXT NOT NULL,
 request_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','waiting_owner','waiting_approval','completed','failed','interrupted')),
 history_json TEXT NOT NULL DEFAULT '[]',
 prepared_form_json TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_browser_takeover_mission_v4
ON agent_mission_browser_takeover_v4(mission_id,mode);

-- Receipts contain hashes and status only; never typed values or credentials.
CREATE TABLE IF NOT EXISTS agent_mission_browser_receipts_v4 (
 task_id TEXT NOT NULL REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
 request_id TEXT NOT NULL,
 mission_id TEXT NOT NULL,
 source_app_key TEXT NOT NULL,
 session_token TEXT NOT NULL,
 lease_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('claimed','succeeded','uncertain')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(task_id,request_id)
);

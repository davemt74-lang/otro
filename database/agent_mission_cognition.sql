-- A4: persist supervisor staffing proposals separately from executable tasks.
-- All proposals require an explicit approving transition before workers exist.
CREATE TABLE IF NOT EXISTS agent_mission_decisions_v1 (
 id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
 source_app_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 basis_hash TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('staff','finish')),
 status TEXT NOT NULL DEFAULT 'proposed'
   CHECK(status IN ('proposed','approved','rejected','no_changes')),
 reason TEXT NOT NULL DEFAULT '',
 confidence INTEGER NOT NULL DEFAULT 0 CHECK(confidence BETWEEN 0 AND 100),
 tasks_json TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 decided_at TEXT,
 UNIQUE(mission_id,request_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_mission_decisions_v1
  ON agent_mission_decisions_v1(mission_id,created_at DESC);

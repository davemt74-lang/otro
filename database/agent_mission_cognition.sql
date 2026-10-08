-- A4 additive cognitive supervision: no change to legacy delegation state.
CREATE TABLE IF NOT EXISTS agent_mission_cognitive_settings_v1 (
  mission_id TEXT PRIMARY KEY REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
  max_review_rounds INTEGER NOT NULL DEFAULT 2 CHECK(max_review_rounds BETWEEN 1 AND 2),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_mission_reviews_v1 (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  review_round INTEGER NOT NULL CHECK(review_round BETWEEN 1 AND 2),
  status TEXT NOT NULL CHECK(status IN ('evaluating','proposed','complete','applied','declined','failed')),
  decision TEXT NOT NULL DEFAULT '',
  rationale TEXT NOT NULL DEFAULT '',
  proposed_tasks_json TEXT NOT NULL DEFAULT '[]',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(mission_id,review_round)
);
CREATE INDEX IF NOT EXISTS idx_agent_mission_reviews_v1_latest
ON agent_mission_reviews_v1(mission_id,review_round DESC);

-- KB5C: owner-opt-in, per-restaurant restart-safe automatic sync policy.
-- Disabled by default; lease_id protects against duplicate workers/restarts.
CREATE TABLE IF NOT EXISTS food_monster_sync_policy (
 connection_id TEXT PRIMARY KEY,
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 interval_seconds INTEGER NOT NULL DEFAULT 21600 CHECK(interval_seconds IN (3600,21600,86400)),
 next_due_at TEXT,
 lease_id TEXT,
 lease_until TEXT,
 generation INTEGER NOT NULL DEFAULT 0,
 failure_count INTEGER NOT NULL DEFAULT 0,
 last_run_at TEXT,
 last_success_at TEXT,
 last_outcome TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (connection_id) REFERENCES food_monster_connections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fm_auto_due
 ON food_monster_sync_policy(enabled,next_due_at,lease_until);

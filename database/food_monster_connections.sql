-- KB3: each linked self-hosted Food Monster is independent. No tokens or
-- source content are ever stored in this SQLite registry.
CREATE TABLE IF NOT EXISTS food_monster_connections (
 id TEXT PRIMARY KEY,
 origin TEXT NOT NULL,
 instance_id TEXT NOT NULL,
 remote_connection_id TEXT NOT NULL,
 label TEXT NOT NULL,
 scopes_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disconnected')),
 added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_checked_at TEXT,
 last_error TEXT NOT NULL DEFAULT '',
 UNIQUE(origin,instance_id),
 UNIQUE(origin,remote_connection_id)
);

CREATE INDEX IF NOT EXISTS idx_food_monster_connection_state ON food_monster_connections(status,added_at);

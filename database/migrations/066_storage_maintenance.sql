CREATE TABLE IF NOT EXISTS storage_policy (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
    minimum_free_bytes INTEGER NOT NULL DEFAULT 5368709120 CHECK (minimum_free_bytes >= 268435456),
    warning_free_percent REAL NOT NULL DEFAULT 15.0 CHECK (warning_free_percent BETWEEN 1.0 AND 50.0),
    critical_free_percent REAL NOT NULL DEFAULT 7.5 CHECK (critical_free_percent BETWEEN 0.5 AND 25.0),
    allow_owner_backup_prune INTEGER NOT NULL DEFAULT 1 CHECK (allow_owner_backup_prune IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO storage_policy(
    singleton_id,minimum_free_bytes,warning_free_percent,critical_free_percent,allow_owner_backup_prune
) VALUES (1,5368709120,15.0,7.5,1)
ON CONFLICT(singleton_id) DO NOTHING;

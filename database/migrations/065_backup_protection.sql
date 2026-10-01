CREATE TABLE IF NOT EXISTS backup_policy (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
    include_app_data INTEGER NOT NULL DEFAULT 1 CHECK (include_app_data IN (0,1)),
    retain_manual INTEGER NOT NULL DEFAULT 10 CHECK (retain_manual BETWEEN 1 AND 100),
    retain_automatic INTEGER NOT NULL DEFAULT 7 CHECK (retain_automatic BETWEEN 1 AND 100),
    retain_pre_restore INTEGER NOT NULL DEFAULT 3 CHECK (retain_pre_restore BETWEEN 1 AND 20),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO backup_policy(
    singleton_id,include_app_data,retain_manual,retain_automatic,retain_pre_restore
) VALUES (1,1,10,7,3)
ON CONFLICT(singleton_id) DO NOTHING;

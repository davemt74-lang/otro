CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT 'null',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO system_settings(setting_key, value_json)
VALUES ('first_run_complete', 'false');

INSERT OR IGNORE INTO system_settings(setting_key, value_json)
VALUES ('first_run_prompted', 'false');

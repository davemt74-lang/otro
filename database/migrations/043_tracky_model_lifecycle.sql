-- Tracky V2.77 — model lifecycle, drift and safe rollout projection.
-- Tracky remains the lifecycle/activation/rollback authority.
-- OTRO persists only the latest governed semantic lifecycle report.

CREATE TABLE IF NOT EXISTS tracky_model_lifecycle (
    id INTEGER PRIMARY KEY CHECK(id=1),
    protocol TEXT NOT NULL DEFAULT 'physical_model_lifecycle.v1',
    schema_version INTEGER NOT NULL DEFAULT 1,
    report_json TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL DEFAULT '',
    generated_at TEXT,
    observed_at TEXT,
    source TEXT NOT NULL DEFAULT 'tracky',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO tracky_model_lifecycle(id) VALUES (1);

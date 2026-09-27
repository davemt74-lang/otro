-- Tracky V2.76 — Forecast calibration & model accuracy projection.
-- The authoritative prediction/settlement ledger remains inside Tracky.
-- OTRO persists only the latest governed semantic calibration report.

CREATE TABLE IF NOT EXISTS tracky_forecast_calibration (
    id INTEGER PRIMARY KEY CHECK(id=1),
    protocol TEXT NOT NULL DEFAULT 'forecast_calibration.v1',
    schema_version INTEGER NOT NULL DEFAULT 1,
    report_json TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL DEFAULT '',
    generated_at TEXT,
    observed_at TEXT,
    source TEXT NOT NULL DEFAULT 'tracky',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO tracky_forecast_calibration(id) VALUES (1);

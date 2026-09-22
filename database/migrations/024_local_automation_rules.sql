PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS automation_runtime_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    poll_seconds INTEGER NOT NULL DEFAULT 15 CHECK (poll_seconds BETWEEN 5 AND 300),
    max_actions_per_run INTEGER NOT NULL DEFAULT 12 CHECK (max_actions_per_run BETWEEN 1 AND 16),
    max_rule_fires_per_minute INTEGER NOT NULL DEFAULT 20 CHECK (max_rule_fires_per_minute BETWEEN 1 AND 60),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO automation_runtime_settings(
    id, enabled, poll_seconds, max_actions_per_run, max_rule_fires_per_minute
) VALUES (1,1,15,12,20)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS automation_routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    approval_mode TEXT NOT NULL DEFAULT 'ask_every_time'
        CHECK (approval_mode IN ('suggest_only','ask_every_time')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_routine_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 16),
    device_id INTEGER NOT NULL,
    command TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE CASCADE,
    FOREIGN KEY(device_id) REFERENCES automation_devices(id) ON DELETE RESTRICT,
    UNIQUE(routine_id, position)
);

CREATE INDEX IF NOT EXISTS idx_automation_routine_steps_routine
ON automation_routine_steps(routine_id, position);

CREATE TABLE IF NOT EXISTS automation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    trigger_kind TEXT NOT NULL
        CHECK (trigger_kind IN ('manual','daily','device_state')),
    trigger_json TEXT NOT NULL DEFAULT '{}',
    conditions_json TEXT NOT NULL DEFAULT '[]',
    routine_id INTEGER NOT NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60 CHECK (cooldown_seconds BETWEEN 0 AND 86400),
    last_condition INTEGER CHECK (last_condition IN (0,1) OR last_condition IS NULL),
    last_evaluated_at TEXT,
    last_fired_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_automation_rules_due
ON automation_rules(enabled, trigger_kind, next_run_at);

CREATE TABLE IF NOT EXISTS automation_rule_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER,
    routine_id INTEGER NOT NULL,
    trigger_kind TEXT NOT NULL,
    trigger_snapshot_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL
        CHECK (status IN ('suggested','requested','skipped','failed')),
    action_count INTEGER NOT NULL DEFAULT 0,
    request_ids_json TEXT NOT NULL DEFAULT '[]',
    suggestion_ids_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(rule_id) REFERENCES automation_rules(id) ON DELETE SET NULL,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_automation_rule_executions_rule
ON automation_rule_executions(rule_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_automation_rule_executions_created
ON automation_rule_executions(created_at DESC);

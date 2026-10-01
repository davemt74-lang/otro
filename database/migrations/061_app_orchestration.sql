PRAGMA foreign_keys = OFF;

CREATE TABLE automation_routine_steps_v061 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 16),
    step_kind TEXT NOT NULL DEFAULT 'device' CHECK (step_kind IN ('device','app_action')),
    device_id INTEGER,
    command TEXT,
    app_key TEXT,
    action_key TEXT,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE CASCADE,
    FOREIGN KEY(device_id) REFERENCES automation_devices(id) ON DELETE RESTRICT,
    UNIQUE(routine_id, position),
    CHECK (
        (step_kind='device' AND device_id IS NOT NULL AND command IS NOT NULL AND app_key IS NULL AND action_key IS NULL)
        OR
        (step_kind='app_action' AND device_id IS NULL AND command IS NULL AND app_key IS NOT NULL AND action_key IS NOT NULL)
    )
);

INSERT INTO automation_routine_steps_v061(
    id,routine_id,position,step_kind,device_id,command,app_key,action_key,arguments_json,created_at
)
SELECT id,routine_id,position,'device',device_id,command,NULL,NULL,arguments_json,created_at
FROM automation_routine_steps;

DROP TABLE automation_routine_steps;
ALTER TABLE automation_routine_steps_v061 RENAME TO automation_routine_steps;

CREATE INDEX idx_automation_routine_steps_routine
ON automation_routine_steps(routine_id, position);

CREATE TABLE automation_rules_v061 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    trigger_kind TEXT NOT NULL
        CHECK (trigger_kind IN ('manual','daily','device_state','app_event')),
    trigger_json TEXT NOT NULL DEFAULT '{}',
    conditions_json TEXT NOT NULL DEFAULT '[]',
    routine_id INTEGER NOT NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60 CHECK (cooldown_seconds BETWEEN 0 AND 86400),
    last_condition INTEGER CHECK (last_condition IN (0,1) OR last_condition IS NULL),
    last_event_id INTEGER,
    last_evaluated_at TEXT,
    last_fired_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE RESTRICT
);

INSERT INTO automation_rules_v061(
    id,rule_key,name,description,enabled,trigger_kind,trigger_json,conditions_json,
    routine_id,cooldown_seconds,last_condition,last_event_id,last_evaluated_at,last_fired_at,
    next_run_at,created_at,updated_at
)
SELECT
    id,rule_key,name,description,enabled,trigger_kind,trigger_json,conditions_json,
    routine_id,cooldown_seconds,last_condition,NULL,last_evaluated_at,last_fired_at,
    next_run_at,created_at,updated_at
FROM automation_rules;

DROP TABLE automation_rules;
ALTER TABLE automation_rules_v061 RENAME TO automation_rules;

CREATE INDEX idx_automation_rules_due
ON automation_rules(enabled, trigger_kind, next_run_at);

CREATE TABLE IF NOT EXISTS automation_app_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER NOT NULL,
    rule_id INTEGER,
    app_key TEXT NOT NULL,
    action_key TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL DEFAULT 'write',
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested','accepted','dismissed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE CASCADE,
    FOREIGN KEY(rule_id) REFERENCES automation_rules(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_app_suggestions_status
ON automation_app_suggestions(status, id DESC);

PRAGMA foreign_keys = ON;

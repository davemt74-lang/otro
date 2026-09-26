-- HomeServer v2.4 Section 4 — governed Tasks & Calendar continuity.
-- Existing paired-app permissions are not modified or auto-granted.

CREATE TABLE action_requests_v35 (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN (
        'memory.write','tasks.create','tasks.update','tasks.delete',
        'files.update','files.delete',
        'vp3.booking.create','vp3.booking.reschedule','vp3.booking.cancel',
        'devices.command',
        'contacts.create','contacts.update','contacts.delete',
        'knowledge.create','knowledge.update','knowledge.delete',
        'calendar.create','calendar.update','calendar.delete'
    )),
    source_app_key TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('owner','app')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','executing','executed','denied','failed','expired')),
    arguments_json TEXT NOT NULL,
    arguments_meta_json TEXT NOT NULL DEFAULT '{}',
    request_tool_run_id INTEGER REFERENCES tool_runs(id) ON DELETE SET NULL,
    execution_tool_run_id INTEGER REFERENCES tool_runs(id) ON DELETE SET NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    executed_at TEXT
);

INSERT INTO action_requests_v35(
    id, action_key, source_app_key, actor_type, status, arguments_json,
    arguments_meta_json, request_tool_run_id, execution_tool_run_id, error,
    created_at, expires_at, decided_at, executed_at
)
SELECT
    id, action_key, source_app_key, actor_type, status, arguments_json,
    arguments_meta_json, request_tool_run_id, execution_tool_run_id, error,
    created_at, expires_at, decided_at, executed_at
FROM action_requests;

DROP TABLE action_requests;
ALTER TABLE action_requests_v35 RENAME TO action_requests;

CREATE INDEX idx_action_requests_status_created ON action_requests(status, created_at DESC);
CREATE INDEX idx_action_requests_source_created ON action_requests(source_app_key, created_at DESC);

CREATE TABLE IF NOT EXISTS local_calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    all_day INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0,1)),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
    source_app_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_local_calendar_active_start ON local_calendar_events(status,start_at,id);

CREATE TABLE IF NOT EXISTS federated_task_mutations (
    source_app_key TEXT NOT NULL,
    mutation_id TEXT NOT NULL,
    action_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    canonical_id TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key, mutation_id)
);
CREATE INDEX IF NOT EXISTS idx_federated_task_mutations_created ON federated_task_mutations(created_at DESC);

CREATE TABLE IF NOT EXISTS federated_calendar_mutations (
    source_app_key TEXT NOT NULL,
    mutation_id TEXT NOT NULL,
    action_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    canonical_id TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key, mutation_id)
);
CREATE INDEX IF NOT EXISTS idx_federated_calendar_mutations_created ON federated_calendar_mutations(created_at DESC);

INSERT INTO tool_policies(tool_key, enabled) VALUES ('tasks.update',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('tasks.delete',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('calendar.list',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('calendar.create',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('calendar.update',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('calendar.delete',1) ON CONFLICT(tool_key) DO NOTHING;

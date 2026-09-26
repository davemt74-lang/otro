-- HomeServer v2.4 Section 6 — Agent Brain & Memory Continuity.
-- Existing memory rows and paired-app permissions are preserved.
-- No memory permission is auto-granted.

CREATE TABLE action_requests_v37 (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN (
        'memory.write','memory.update','memory.delete',
        'tasks.create','tasks.update','tasks.delete',
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

INSERT INTO action_requests_v37(
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
ALTER TABLE action_requests_v37 RENAME TO action_requests;

CREATE INDEX idx_action_requests_status_created ON action_requests(status, created_at DESC);
CREATE INDEX idx_action_requests_source_created ON action_requests(source_app_key, created_at DESC);

CREATE TABLE IF NOT EXISTS federated_memory_mutations (
    source_app_key TEXT NOT NULL,
    mutation_id TEXT NOT NULL,
    action_key TEXT NOT NULL CHECK (action_key IN ('memory.write','memory.update','memory.delete')),
    request_hash TEXT NOT NULL,
    canonical_id TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key, mutation_id)
);
CREATE INDEX IF NOT EXISTS idx_federated_memory_mutations_created
    ON federated_memory_mutations(created_at DESC);

INSERT INTO tool_policies(tool_key, enabled) VALUES ('memory.update',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('memory.delete',1) ON CONFLICT(tool_key) DO NOTHING;

CREATE TABLE workspace_sync_actions (
    mutation_id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, session_hash TEXT NOT NULL,
    authority_hash TEXT NOT NULL, dataset TEXT NOT NULL, record_key TEXT NOT NULL,
    expected_revision TEXT NOT NULL, fields_json TEXT NOT NULL, request_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','sending','applied','synced','superseded','conflict','blocked','failed','cancelled')),
    error TEXT NOT NULL DEFAULT '', receipt_revision TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX workspace_sync_actions_peer ON workspace_sync_actions(peer_id,state,created_at);

CREATE TABLE action_requests_v69 (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN (
        'memory.write','memory.update','memory.delete',
        'tasks.create','tasks.update','tasks.delete',
        'files.update','files.delete',
        'vp3.booking.create','vp3.booking.reschedule','vp3.booking.cancel',
        'devices.command',
        'contacts.create','contacts.update','contacts.delete',
        'knowledge.create','knowledge.update','knowledge.delete',
        'calendar.create','calendar.update','calendar.delete','workspace.update'
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

INSERT INTO action_requests_v69(
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
ALTER TABLE action_requests_v69 RENAME TO action_requests;

CREATE INDEX idx_action_requests_status_created ON action_requests(status, created_at DESC);
CREATE INDEX idx_action_requests_source_created ON action_requests(source_app_key, created_at DESC);


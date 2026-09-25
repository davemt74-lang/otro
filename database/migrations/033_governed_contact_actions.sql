-- HomeServer v2.4 Section 2 — governed contact mutations.
--
-- SQLite cannot widen the action_requests CHECK constraint in place. Rebuild
-- the approval queue so all prior requests remain intact while contacts
-- create/update/delete become governed write actions. This migration does NOT
-- grant contacts.write to any existing paired application.
CREATE TABLE action_requests_v33 (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN (
        'memory.write','tasks.create','files.update','files.delete',
        'vp3.booking.create','vp3.booking.reschedule','vp3.booking.cancel',
        'devices.command',
        'contacts.create','contacts.update','contacts.delete'
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

INSERT INTO action_requests_v33(
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
ALTER TABLE action_requests_v33 RENAME TO action_requests;

CREATE INDEX idx_action_requests_status_created
ON action_requests(status, created_at DESC);
CREATE INDEX idx_action_requests_source_created
ON action_requests(source_app_key, created_at DESC);

INSERT INTO tool_policies(tool_key, enabled)
VALUES ('contacts.create', 1)
ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled)
VALUES ('contacts.update', 1)
ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled)
VALUES ('contacts.delete', 1)
ON CONFLICT(tool_key) DO NOTHING;

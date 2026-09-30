-- HomeServer Apps V1 Section 8 — governed Agent app administration.
-- Preserve all existing approval requests while widening the action-key CHECK
-- for owner-approved HomeServer Apps lifecycle and release actions.
CREATE TABLE action_requests_v60 (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN (
        'memory.write','memory.update','memory.delete',
        'tasks.create','tasks.update','tasks.delete',
        'files.update','files.delete',
        'vp3.booking.create','vp3.booking.reschedule','vp3.booking.cancel',
        'devices.command',
        'contacts.create','contacts.update','contacts.delete',
        'knowledge.create','knowledge.update','knowledge.delete',
        'calendar.create','calendar.update','calendar.delete',
        'apps.prebuilt.install','apps.build_install','apps.rollback',
        'apps.recover','apps.start','apps.stop'
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

INSERT INTO action_requests_v60(
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
ALTER TABLE action_requests_v60 RENAME TO action_requests;

CREATE INDEX idx_action_requests_status_created
ON action_requests(status, created_at DESC);
CREATE INDEX idx_action_requests_source_created
ON action_requests(source_app_key, created_at DESC);

INSERT INTO tool_policies(tool_key, enabled) VALUES ('apps.prebuilt.install',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('apps.build_install',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('apps.rollback',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('apps.recover',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('apps.start',1) ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled) VALUES ('apps.stop',1) ON CONFLICT(tool_key) DO NOTHING;

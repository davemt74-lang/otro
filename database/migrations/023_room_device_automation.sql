PRAGMA foreign_keys = ON;

-- SQLite cannot widen the action_requests CHECK constraint in place. Rebuild
-- the approval queue so every existing approval type remains intact while
-- v0.60 adds the approval-only physical device command.
CREATE TABLE action_requests_v23 (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN (
        'memory.write','tasks.create','files.update','files.delete',
        'vp3.booking.create','vp3.booking.reschedule','vp3.booking.cancel',
        'devices.command'
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

INSERT INTO action_requests_v23(
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
ALTER TABLE action_requests_v23 RENAME TO action_requests;

CREATE INDEX idx_action_requests_status_created
ON action_requests(status, created_at DESC);
CREATE INDEX idx_action_requests_source_created
ON action_requests(source_app_key, created_at DESC);

INSERT INTO tool_policies(tool_key, enabled)
VALUES ('devices.list', 1)
ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled)
VALUES ('devices.command', 1)
ON CONFLICT(tool_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS automation_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    executable INTEGER NOT NULL DEFAULT 0 CHECK (executable IN (0,1)),
    status TEXT NOT NULL DEFAULT 'disconnected'
        CHECK (status IN ('connected','disconnected','degraded','disabled')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_key TEXT NOT NULL UNIQUE,
    provider_key TEXT NOT NULL,
    provider_device_id TEXT NOT NULL,
    room_id INTEGER,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    controllable INTEGER NOT NULL DEFAULT 0 CHECK (controllable IN (0,1)),
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(room_id) REFERENCES automation_rooms(id) ON DELETE SET NULL,
    UNIQUE(provider_key, provider_device_id)
);

CREATE INDEX IF NOT EXISTS idx_automation_devices_room
ON automation_devices(room_id, enabled, category);

CREATE INDEX IF NOT EXISTS idx_automation_devices_provider
ON automation_devices(provider_key, enabled);

CREATE TABLE IF NOT EXISTS automation_device_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    action_request_id TEXT,
    source_app_key TEXT NOT NULL,
    command TEXT NOT NULL,
    arguments_meta_json TEXT NOT NULL DEFAULT '{}',
    before_state_json TEXT NOT NULL DEFAULT '{}',
    after_state_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('executing','completed','failed','blocked')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(device_id) REFERENCES automation_devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automation_device_actions_device
ON automation_device_actions(device_id, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_device_actions_request
ON automation_device_actions(action_request_id)
WHERE action_request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS automation_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL,
    source_event_type TEXT,
    device_id INTEGER,
    command TEXT,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested','requested','dismissed','expired')),
    action_request_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(device_id) REFERENCES automation_devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automation_suggestions_status
ON automation_suggestions(status, id DESC);

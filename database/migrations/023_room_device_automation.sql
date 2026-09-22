PRAGMA foreign_keys = ON;

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

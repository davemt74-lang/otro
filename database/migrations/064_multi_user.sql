CREATE TABLE IF NOT EXISTS homeserver_members (
    member_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member','guest')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_homeserver_members_status_role
ON homeserver_members(status, role, display_name);

CREATE TABLE IF NOT EXISTS homeserver_member_sessions (
    session_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(member_id) REFERENCES homeserver_members(member_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_homeserver_member_sessions_member
ON homeserver_member_sessions(member_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS homeserver_member_app_access (
    member_id TEXT NOT NULL,
    app_key TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 1 CHECK (allowed IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(member_id, app_key),
    FOREIGN KEY(member_id) REFERENCES homeserver_members(member_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS homeserver_member_context (
    member_id TEXT NOT NULL,
    context_key TEXT NOT NULL,
    value_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(member_id, context_key),
    FOREIGN KEY(member_id) REFERENCES homeserver_members(member_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS homeserver_member_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_key TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(member_id) REFERENCES homeserver_members(member_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_homeserver_member_activity_member
ON homeserver_member_activity(member_id, id DESC);

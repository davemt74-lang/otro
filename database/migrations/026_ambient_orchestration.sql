PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS orchestration_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    poll_seconds INTEGER NOT NULL DEFAULT 30 CHECK (poll_seconds BETWEEN 10 AND 300),
    suggestion_cooldown_seconds INTEGER NOT NULL DEFAULT 14400
        CHECK (suggestion_cooldown_seconds BETWEEN 300 AND 604800),
    max_open_sessions INTEGER NOT NULL DEFAULT 12 CHECK (max_open_sessions BETWEEN 1 AND 50),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO orchestration_settings(
    id,enabled,poll_seconds,suggestion_cooldown_seconds,max_open_sessions
) VALUES (1,1,30,14400,12)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS orchestration_modes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    routine_id INTEGER NOT NULL,
    room_keys_json TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    suggest_trigger_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(routine_id) REFERENCES automation_routines(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_orchestration_modes_priority
ON orchestration_modes(enabled,priority DESC,name);

CREATE TABLE IF NOT EXISTS orchestration_mode_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('suggested','requested','active','suspended','ended','failed')
    ),
    source_kind TEXT NOT NULL,
    reason TEXT,
    request_ids_json TEXT NOT NULL DEFAULT '[]',
    context_snapshot_json TEXT NOT NULL DEFAULT '{}',
    superseded_by_session_id INTEGER,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requested_at TEXT,
    active_at TEXT,
    suspended_at TEXT,
    ended_at TEXT,
    failure_reason TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(mode_id) REFERENCES orchestration_modes(id) ON DELETE RESTRICT,
    FOREIGN KEY(superseded_by_session_id)
        REFERENCES orchestration_mode_sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_orchestration_sessions_open
ON orchestration_mode_sessions(state,mode_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS orchestration_mode_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    mode_id INTEGER NOT NULL,
    conflicting_session_id INTEGER NOT NULL,
    conflicting_mode_id INTEGER NOT NULL,
    shared_devices_json TEXT NOT NULL DEFAULT '[]',
    resolution TEXT NOT NULL CHECK (
        resolution IN ('blocked','superseded','acknowledged')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES orchestration_mode_sessions(id)
        ON DELETE SET NULL,
    FOREIGN KEY(mode_id) REFERENCES orchestration_modes(id) ON DELETE CASCADE,
    FOREIGN KEY(conflicting_session_id)
        REFERENCES orchestration_mode_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(conflicting_mode_id)
        REFERENCES orchestration_modes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orchestration_conflicts_mode
ON orchestration_mode_conflicts(mode_id,created_at DESC);

CREATE TABLE IF NOT EXISTS orchestration_mode_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES orchestration_mode_sessions(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orchestration_transitions_session
ON orchestration_mode_transitions(session_id,id DESC);

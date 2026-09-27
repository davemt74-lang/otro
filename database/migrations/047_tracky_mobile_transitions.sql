CREATE TABLE IF NOT EXISTS tracky_mobile_transitions (
  transition_id TEXT PRIMARY KEY,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  subject_scope TEXT NOT NULL,
  source_site_id TEXT NOT NULL,
  destination_site_id TEXT NULL,
  state TEXT NOT NULL,
  previous_state TEXT NULL,
  resume_state TEXT NULL,
  state_reason TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  destination_confidence REAL NOT NULL DEFAULT 0,
  temporary_context_json TEXT NOT NULL DEFAULT 'null',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  revision INTEGER NOT NULL,
  identity_linking INTEGER NOT NULL DEFAULT 0,
  authority_scope TEXT NOT NULL DEFAULT 'source_site',
  origin_role TEXT NOT NULL DEFAULT 'local_authority',
  fingerprint TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  state_changed_at INTEGER NOT NULL,
  observed_updated_at INTEGER NOT NULL,
  arrived_at INTEGER NULL,
  canceled_at INTEGER NULL,
  offline_since INTEGER NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(destination_site_id) REFERENCES tracky_sites(site_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tracky_mobile_transition_subject
ON tracky_mobile_transitions(subject_kind,subject_id,observed_updated_at);

CREATE INDEX IF NOT EXISTS idx_tracky_mobile_transition_sites
ON tracky_mobile_transitions(source_site_id,destination_site_id,state);

CREATE INDEX IF NOT EXISTS idx_tracky_mobile_transition_state
ON tracky_mobile_transitions(state,observed_updated_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tracky_mobile_transition_active_subject
ON tracky_mobile_transitions(subject_kind,subject_id)
WHERE state NOT IN ('arrived','canceled');

CREATE TABLE IF NOT EXISTS tracky_mobile_transition_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transition_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  state TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'tracky',
  origin_role TEXT NOT NULL DEFAULT 'local_authority',
  snapshot_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(transition_id,revision),
  FOREIGN KEY(transition_id) REFERENCES tracky_mobile_transitions(transition_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_mobile_transition_history_recent
ON tracky_mobile_transition_history(transition_id,revision DESC);

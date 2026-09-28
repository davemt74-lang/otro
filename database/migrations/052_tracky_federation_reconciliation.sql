CREATE TABLE IF NOT EXISTS tracky_federation_reconciliation_state (
  remote_site_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'unknown',
  last_contact_at TEXT NOT NULL DEFAULT '',
  partitioned_at TEXT NOT NULL DEFAULT '',
  stale_since TEXT NOT NULL DEFAULT '',
  reconciling_since TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT NOT NULL DEFAULT '',
  local_revision INTEGER NOT NULL DEFAULT 0,
  local_fingerprint TEXT NOT NULL DEFAULT '',
  local_authority_epoch INTEGER NOT NULL DEFAULT 0,
  remote_revision INTEGER NOT NULL DEFAULT 0,
  remote_fingerprint TEXT NOT NULL DEFAULT '',
  remote_authority_epoch INTEGER NOT NULL DEFAULT 0,
  last_reconciliation_id TEXT NOT NULL DEFAULT '',
  last_reconciled_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(remote_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_federation_reconciliation_status
ON tracky_federation_reconciliation_state(status,updated_at DESC);

CREATE TABLE IF NOT EXISTS tracky_federation_reconciliation_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reconciliation_id TEXT NOT NULL,
  remote_site_id TEXT NOT NULL,
  request_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  local_revision INTEGER NOT NULL DEFAULT 0,
  remote_revision INTEGER NOT NULL DEFAULT 0,
  applied_revision INTEGER NOT NULL DEFAULT 0,
  authority_epoch INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT NOT NULL DEFAULT '',
  UNIQUE(reconciliation_id),
  FOREIGN KEY(remote_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_federation_reconciliation_runs_site
ON tracky_federation_reconciliation_runs(remote_site_id,id DESC);

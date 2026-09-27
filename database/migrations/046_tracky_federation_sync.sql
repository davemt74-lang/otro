CREATE TABLE IF NOT EXISTS tracky_federation_identity (
  id INTEGER PRIMARY KEY CHECK(id=1),
  local_site_id TEXT NULL,
  pinned_at TEXT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(local_site_id) REFERENCES tracky_sites(site_id) ON DELETE SET NULL
);

INSERT OR IGNORE INTO tracky_federation_identity(id,local_site_id) VALUES (1,NULL);

CREATE TABLE IF NOT EXISTS tracky_federation_sync_peers (
  remote_site_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'idle',
  last_received_revision INTEGER NOT NULL DEFAULT 0,
  last_received_fingerprint TEXT NOT NULL DEFAULT '',
  last_received_authority_epoch INTEGER NOT NULL DEFAULT 0,
  last_received_at TEXT NULL,
  quarantined_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(remote_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracky_federation_outbound_ack (
  destination_site_id TEXT NOT NULL,
  source_site_id TEXT NOT NULL,
  acknowledged_revision INTEGER NOT NULL DEFAULT 0,
  acknowledged_fingerprint TEXT NOT NULL DEFAULT '',
  acknowledged_at TEXT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(destination_site_id,source_site_id),
  FOREIGN KEY(destination_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(source_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracky_federation_quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  envelope_id TEXT NOT NULL DEFAULT '',
  source_site_id TEXT NOT NULL DEFAULT '',
  destination_site_id TEXT NOT NULL DEFAULT '',
  source_world_revision INTEGER NOT NULL DEFAULT 0,
  source_authority_epoch INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  envelope_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracky_federation_quarantine_status
ON tracky_federation_quarantine(status,created_at);

CREATE INDEX IF NOT EXISTS idx_tracky_federation_quarantine_source
ON tracky_federation_quarantine(source_site_id,source_world_revision);

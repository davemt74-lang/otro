CREATE TABLE IF NOT EXISTS tracky_canonical_identities (
  canonical_identity_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  aliases_json TEXT NOT NULL DEFAULT '[]',
  members_json TEXT NOT NULL DEFAULT '[]',
  revision INTEGER NOT NULL DEFAULT 1,
  origin_role TEXT NOT NULL DEFAULT 'local_governed',
  governing_site_id TEXT NULL,
  created_at_ms INTEGER NOT NULL DEFAULT 0,
  observed_updated_at_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(governing_site_id) REFERENCES tracky_sites(site_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tracky_identity_status
ON tracky_canonical_identities(status,entity_type,updated_at);

CREATE TABLE IF NOT EXISTS tracky_identity_links (
  link_id TEXT PRIMARY KEY,
  pair_key TEXT NOT NULL UNIQUE,
  canonical_identity_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  left_ref TEXT NOT NULL,
  right_ref TEXT NOT NULL,
  left_site_id TEXT NOT NULL,
  right_site_id TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0,
  auto_confirmed INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1,
  origin_role TEXT NOT NULL DEFAULT 'local_governed',
  governing_site_id TEXT NOT NULL,
  governing_authority_device_id TEXT NOT NULL,
  governing_authority_epoch INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL DEFAULT 0,
  observed_updated_at_ms INTEGER NOT NULL DEFAULT 0,
  confirmed_at_ms INTEGER NULL,
  rejected_at_ms INTEGER NULL,
  revoked_at_ms INTEGER NULL,
  split_at_ms INTEGER NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(canonical_identity_id) REFERENCES tracky_canonical_identities(canonical_identity_id) ON DELETE CASCADE,
  FOREIGN KEY(governing_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(governing_authority_device_id) REFERENCES tracky_site_devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_identity_link_canonical
ON tracky_identity_links(canonical_identity_id,status);

CREATE INDEX IF NOT EXISTS idx_tracky_identity_link_sites
ON tracky_identity_links(left_site_id,right_site_id,status);

CREATE TABLE IF NOT EXISTS tracky_identity_blocked_pairs (
  pair_key TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  blocked_at_ms INTEGER NOT NULL DEFAULT 0,
  source_link_id TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracky_identity_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  link_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  status TEXT NOT NULL,
  origin_role TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(link_id,revision),
  FOREIGN KEY(link_id) REFERENCES tracky_identity_links(link_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_identity_history_recent
ON tracky_identity_history(link_id,revision DESC);

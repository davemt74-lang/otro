CREATE TABLE IF NOT EXISTS tracky_federation_policy_state (
  id INTEGER PRIMARY KEY CHECK(id=1),
  revision INTEGER NOT NULL DEFAULT 0,
  revocation_epoch INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO tracky_federation_policy_state(id,revision,revocation_epoch) VALUES (1,0,0);

CREATE TABLE IF NOT EXISTS tracky_federation_site_policies (
  site_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL DEFAULT 1,
  mode TEXT NOT NULL DEFAULT 'private',
  allow_federation INTEGER NOT NULL DEFAULT 0,
  allow_remote_observation INTEGER NOT NULL DEFAULT 0,
  default_identity_visibility TEXT NOT NULL DEFAULT 'none',
  allowed_peer_sites_json TEXT NOT NULL DEFAULT '[]',
  origin_role TEXT NOT NULL DEFAULT 'local_governed',
  governing_authority_device_id TEXT NULL,
  governing_authority_epoch INTEGER NOT NULL DEFAULT 0,
  observed_updated_at_ms INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracky_federation_permissions (
  source_site_id TEXT NOT NULL,
  destination_site_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'revoked',
  revision INTEGER NOT NULL DEFAULT 1,
  reason TEXT NOT NULL DEFAULT '',
  origin_role TEXT NOT NULL DEFAULT 'local_governed',
  governing_authority_device_id TEXT NULL,
  governing_authority_epoch INTEGER NOT NULL DEFAULT 0,
  granted_at_ms INTEGER NULL,
  revoked_at_ms INTEGER NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(source_site_id,destination_site_id,scope),
  FOREIGN KEY(source_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(destination_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracky_recognition_consents (
  site_id TEXT NOT NULL,
  canonical_identity_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  revision INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'user',
  reason TEXT NOT NULL DEFAULT '',
  origin_role TEXT NOT NULL DEFAULT 'local_governed',
  governing_authority_device_id TEXT NULL,
  governing_authority_epoch INTEGER NOT NULL DEFAULT 0,
  decided_at_ms INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(site_id,canonical_identity_id,scope),
  FOREIGN KEY(site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracky_federation_policy_revocations (
  revocation_key TEXT PRIMARY KEY,
  governing_site_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  revocation_epoch INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  origin_role TEXT NOT NULL DEFAULT 'local_governed',
  revoked_at_ms INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(governing_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracky_federation_policy_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  governing_site_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  revocation_epoch INTEGER NOT NULL DEFAULT 0,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(governing_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_federation_permissions_destination
ON tracky_federation_permissions(destination_site_id,source_site_id,status);

CREATE INDEX IF NOT EXISTS idx_tracky_recognition_consents_identity
ON tracky_recognition_consents(canonical_identity_id,site_id,status);

CREATE INDEX IF NOT EXISTS idx_tracky_federation_policy_history_site
ON tracky_federation_policy_history(governing_site_id,id DESC);

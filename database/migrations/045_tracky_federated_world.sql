CREATE TABLE IF NOT EXISTS tracky_federated_world_fragments (
  site_id TEXT PRIMARY KEY,
  authority_device_id TEXT NOT NULL,
  authority_epoch INTEGER NOT NULL,
  topology_revision INTEGER NOT NULL DEFAULT 0,
  world_revision INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  fragment_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'tracky',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(authority_device_id) REFERENCES tracky_site_devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_federated_world_authority
ON tracky_federated_world_fragments(authority_device_id,authority_epoch);

CREATE INDEX IF NOT EXISTS idx_tracky_federated_world_updated
ON tracky_federated_world_fragments(updated_at);

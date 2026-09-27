CREATE TABLE IF NOT EXISTS tracky_federated_world_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  world_revision INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  authority_device_id TEXT NOT NULL,
  authority_epoch INTEGER NOT NULL,
  topology_revision INTEGER NOT NULL DEFAULT 0,
  observed_at TEXT NOT NULL DEFAULT '',
  fragment_json TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'tracky',
  privacy_redaction INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(site_id,world_revision,fingerprint),
  FOREIGN KEY(site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(authority_device_id) REFERENCES tracky_site_devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_federated_history_site_revision
ON tracky_federated_world_history(site_id,world_revision DESC,id DESC);

CREATE INDEX IF NOT EXISTS idx_tracky_federated_history_observed
ON tracky_federated_world_history(site_id,observed_at DESC,id DESC);

CREATE TABLE IF NOT EXISTS tracky_federated_query_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_id TEXT NOT NULL,
  intent TEXT NOT NULL,
  local_site_id TEXT NOT NULL,
  requested_sites_json TEXT NOT NULL DEFAULT '[]',
  target_ref TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  denied_json TEXT NOT NULL DEFAULT '[]',
  result_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(local_site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tracky_federated_query_audit_created
ON tracky_federated_query_audit(created_at DESC,id DESC);

INSERT OR IGNORE INTO tracky_federated_world_history(
  site_id,world_revision,fingerprint,authority_device_id,authority_epoch,topology_revision,
  observed_at,fragment_json,source,privacy_redaction
)
SELECT
  site_id,world_revision,fingerprint,authority_device_id,authority_epoch,topology_revision,
  observed_at,fragment_json,source,0
FROM tracky_federated_world_fragments;

CREATE TABLE IF NOT EXISTS tracky_sites (
  site_id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'physical_site',
  status TEXT NOT NULL DEFAULT 'active',
  aliases_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracky_site_devices (
  device_id TEXT PRIMARY KEY,
  site_id TEXT NULL,
  label TEXT NOT NULL,
  hardware_profile TEXT NOT NULL,
  hardware_profile_label TEXT NOT NULL,
  mobility TEXT NOT NULL DEFAULT 'unknown',
  trust_state TEXT NOT NULL DEFAULT 'pending',
  roles_json TEXT NOT NULL DEFAULT '[]',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  aliases_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES tracky_sites(site_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tracky_site_devices_site ON tracky_site_devices(site_id);
CREATE INDEX IF NOT EXISTS idx_tracky_site_devices_trust ON tracky_site_devices(trust_state);

CREATE TABLE IF NOT EXISTS tracky_site_authority (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  authority_epoch INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  released_at TEXT NULL,
  release_reason TEXT NOT NULL DEFAULT '',
  UNIQUE(site_id, authority_epoch),
  FOREIGN KEY(site_id) REFERENCES tracky_sites(site_id) ON DELETE CASCADE,
  FOREIGN KEY(device_id) REFERENCES tracky_site_devices(device_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tracky_site_active_authority
ON tracky_site_authority(site_id) WHERE active=1;

CREATE TABLE IF NOT EXISTS tracky_site_relationships (
  relationship_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_site_relationship_subject ON tracky_site_relationships(subject_id);
CREATE INDEX IF NOT EXISTS idx_tracky_site_relationship_object ON tracky_site_relationships(object_id);

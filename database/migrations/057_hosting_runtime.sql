CREATE TABLE IF NOT EXISTS hosting_sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  requested_hostname TEXT,
  hosting_target TEXT NOT NULL DEFAULT 'homeserver',
  runtime_kind TEXT NOT NULL DEFAULT 'static',
  state TEXT NOT NULL DEFAULT 'configured',
  database_kind TEXT NOT NULL DEFAULT 'sqlite',
  database_relpath TEXT NOT NULL DEFAULT 'database/site.sqlite',
  storage_limit_bytes INTEGER,
  sqlite_limit_bytes INTEGER,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK(hosting_target='homeserver'),
  CHECK(database_kind='sqlite'),
  CHECK(state IN ('configured','active','suspended','failed','deleted'))
);
CREATE INDEX IF NOT EXISTS idx_hosting_sites_state ON hosting_sites(state,updated_at,id);

CREATE TABLE IF NOT EXISTS hosting_runtime_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  state TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES hosting_sites(site_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hosting_runtime_events_site ON hosting_runtime_events(site_id,created_at,id);

CREATE TABLE IF NOT EXISTS hosting_usage_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  storage_bytes INTEGER NOT NULL DEFAULT 0,
  sqlite_bytes INTEGER NOT NULL DEFAULT 0,
  sampled_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES hosting_sites(site_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hosting_usage_samples_site ON hosting_usage_samples(site_id,sampled_at,id);

CREATE TABLE IF NOT EXISTS hosting_backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  backup_id TEXT NOT NULL UNIQUE,
  site_id TEXT NOT NULL,
  sqlite_relpath TEXT NOT NULL,
  sqlite_bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES hosting_sites(site_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hosting_backups_site ON hosting_backups(site_id,created_at,id);
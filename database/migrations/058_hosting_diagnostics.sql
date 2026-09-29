CREATE TABLE IF NOT EXISTS hosting_request_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'public',
  method TEXT NOT NULL,
  path_hint TEXT NOT NULL DEFAULT '/',
  status_code INTEGER NOT NULL,
  duration_ms REAL NOT NULL DEFAULT 0,
  response_bytes INTEGER NOT NULL DEFAULT 0,
  error_class TEXT NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(site_id) REFERENCES hosting_sites(site_id) ON DELETE CASCADE,
  CHECK(source IN ('public','preview','health')),
  CHECK(status_code BETWEEN 100 AND 599),
  CHECK(duration_ms >= 0),
  CHECK(response_bytes >= 0)
);
CREATE INDEX IF NOT EXISTS idx_hosting_request_obs_site_time
  ON hosting_request_observations(site_id,created_at,id);
CREATE INDEX IF NOT EXISTS idx_hosting_request_obs_site_status
  ON hosting_request_observations(site_id,status_code,created_at,id);

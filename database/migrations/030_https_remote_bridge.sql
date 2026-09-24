ALTER TABLE remote_bridge_settings ADD COLUMN transport TEXT NOT NULL DEFAULT 'custom_websocket';
ALTER TABLE remote_bridge_settings ADD COLUMN https_endpoint TEXT NOT NULL DEFAULT '';

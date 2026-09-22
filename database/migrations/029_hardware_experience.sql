PRAGMA foreign_keys = ON;

ALTER TABLE vp3_fleet_inventory
ADD COLUMN hardware_experience_version TEXT NOT NULL DEFAULT '';

ALTER TABLE vp3_fleet_inventory
ADD COLUMN experience_profile TEXT NOT NULL DEFAULT 'generic';

CREATE TABLE IF NOT EXISTS vp3_hardware_experience_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    brightness_percent INTEGER NOT NULL DEFAULT 70 CHECK (brightness_percent BETWEEN 0 AND 100),
    volume_percent INTEGER NOT NULL DEFAULT 65 CHECK (volume_percent BETWEEN 0 AND 100),
    led_intensity_percent INTEGER NOT NULL DEFAULT 70 CHECK (led_intensity_percent BETWEEN 0 AND 100),
    screen_timeout_seconds INTEGER NOT NULL DEFAULT 300 CHECK (screen_timeout_seconds BETWEEN 15 AND 86400),
    wake_behavior TEXT NOT NULL DEFAULT 'presence'
        CHECK (wake_behavior IN ('manual','presence','always_on')),
    agent_button_action TEXT NOT NULL DEFAULT 'push_to_talk'
        CHECK (agent_button_action IN ('push_to_talk','ask_agent','none')),
    hold_action TEXT NOT NULL DEFAULT 'cancel'
        CHECK (hold_action IN ('cancel','meeting_toggle','privacy_hint','none')),
    display_detail TEXT NOT NULL DEFAULT 'standard'
        CHECK (display_detail IN ('minimal','standard','detailed')),
    quiet_visuals INTEGER NOT NULL DEFAULT 0 CHECK (quiet_visuals IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO vp3_hardware_experience_settings(
    id,enabled,brightness_percent,volume_percent,led_intensity_percent,
    screen_timeout_seconds,wake_behavior,agent_button_action,hold_action,
    display_detail,quiet_visuals
) VALUES (1,1,70,65,70,300,'presence','push_to_talk','cancel','standard',0)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS vp3_hardware_experience_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'hardware',
    handled INTEGER NOT NULL DEFAULT 0 CHECK (handled IN (0,1)),
    outcome TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_hardware_experience_events_created
ON vp3_hardware_experience_events(created_at DESC);

CREATE TABLE IF NOT EXISTS vp3_hardware_experience_cards (
    card_key TEXT PRIMARY KEY,
    card_type TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','dismissed','expired')),
    priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_hardware_experience_cards_priority
ON vp3_hardware_experience_cards(state,priority DESC,updated_at DESC);

CREATE TABLE IF NOT EXISTS vp3_hardware_experience_certifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key TEXT NOT NULL,
    os_version TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('passed','degraded','failed')),
    checks_json TEXT NOT NULL DEFAULT '{}',
    certified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_hardware_experience_certifications
ON vp3_hardware_experience_certifications(profile_key,certified_at DESC);

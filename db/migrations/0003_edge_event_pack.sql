CREATE TABLE IF NOT EXISTS camera_model_settings (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  model_path TEXT NOT NULL DEFAULT '',
  confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.35,
  poll_sec INT NOT NULL DEFAULT 2,
  cooldown_sec INT NOT NULL DEFAULT 10,
  timeout_sec INT NOT NULL DEFAULT 5,
  extra_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS camera_event_pack_settings (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  pack_id TEXT NOT NULL DEFAULT 'edge-basic',
  pack_version TEXT NOT NULL DEFAULT '1.0.0',
  params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO app_settings (key, value_json)
VALUES ('webrtc', '{"enabled": true}'::jsonb)
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value_json)
VALUES (
  'person_event_rule',
  '{"enabled": true, "dwellSec": 5, "cooldownSec": 10, "eventType": "person_detected", "severity": "high"}'::jsonb
)
ON CONFLICT (key) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_camera_model_settings_enabled
  ON camera_model_settings (enabled);

CREATE INDEX IF NOT EXISTS idx_camera_event_pack_settings_enabled
  ON camera_event_pack_settings (enabled);

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE cameras (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  rtsp_url TEXT NOT NULL,
  onvif_profile TEXT,
  webrtc_path TEXT NOT NULL UNIQUE,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  status TEXT NOT NULL DEFAULT 'offline',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE event_policies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('clip', 'snapshot')),
  clip_pre_sec INT NOT NULL DEFAULT 10,
  clip_post_sec INT NOT NULL DEFAULT 20,
  clip_cooldown_sec INT NOT NULL DEFAULT 5,
  clip_merge_window_sec INT NOT NULL DEFAULT 3,
  snapshot_count INT NOT NULL DEFAULT 1,
  snapshot_interval_ms INT NOT NULL DEFAULT 0,
  snapshot_format TEXT NOT NULL DEFAULT 'jpg',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (camera_id, event_type)
);

CREATE TABLE destinations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL CHECK (type IN ('https_post', 'sftp')),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  config_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE routing_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  artifact_kind TEXT NOT NULL DEFAULT 'both' CHECK (artifact_kind IN ('clip', 'snapshot', 'both')),
  destination_id UUID NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (camera_id, event_type, artifact_kind, destination_id)
);

CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'medium',
  occurred_at TIMESTAMPTZ NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE camera_rois (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  zones_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE app_settings (
  key TEXT PRIMARY KEY,
  value_json JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO app_settings (key, value_json)
VALUES (
  'ai_model',
  '{
    "enabled": false,
    "modelPath": "",
    "timeoutSec": 5,
    "pollSec": 2,
    "cooldownSec": 10
  }'::jsonb
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value_json)
VALUES (
  'person_event_rule',
  '{
    "enabled": true,
    "dwellSec": 5,
    "cooldownSec": 10,
    "eventType": "person_detected",
    "severity": "high"
  }'::jsonb
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value_json)
VALUES ('webrtc', '{"enabled": true}'::jsonb)
ON CONFLICT (key) DO NOTHING;

CREATE TABLE camera_model_settings (
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

CREATE TABLE camera_event_pack_settings (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  pack_id TEXT NOT NULL DEFAULT 'edge-basic',
  pack_version TEXT NOT NULL DEFAULT '1.0.0',
  params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('clip', 'snapshot')),
  local_path TEXT NOT NULL,
  uri TEXT,
  mime_type TEXT NOT NULL,
  checksum_sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE delivery_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  destination_id UUID NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('queued', 'in_progress', 'success', 'failed')),
  attempt_no INT NOT NULL DEFAULT 1,
  http_status INT,
  error_message TEXT,
  next_retry_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ai_detection_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  trigger BOOLEAN NOT NULL,
  score DOUBLE PRECISION,
  label TEXT,
  detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ai_camera_state (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  last_triggered_at TIMESTAMPTZ
);

CREATE TABLE recorder_camera_health (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  connected BOOLEAN NOT NULL DEFAULT FALSE,
  last_connect_reason TEXT,
  ring_running BOOLEAN NOT NULL DEFAULT FALSE,
  ring_restart_count INT NOT NULL DEFAULT 0,
  last_ring_exit_code INT,
  last_probe_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_camera_time ON events (camera_id, occurred_at DESC);
CREATE INDEX idx_camera_rois_enabled ON camera_rois (enabled);
CREATE INDEX idx_recorder_camera_health_connected ON recorder_camera_health (connected);
CREATE INDEX idx_artifacts_event ON artifacts (event_id);
CREATE INDEX idx_delivery_attempts_status_retry ON delivery_attempts (status, next_retry_at);
CREATE INDEX idx_ai_detection_logs_camera_time ON ai_detection_logs (camera_id, created_at DESC);
CREATE INDEX idx_camera_model_settings_enabled ON camera_model_settings (enabled);
CREATE INDEX idx_camera_event_pack_settings_enabled ON camera_event_pack_settings (enabled);

CREATE TABLE IF NOT EXISTS recorder_camera_health (
  camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  connected BOOLEAN NOT NULL DEFAULT FALSE,
  last_connect_reason TEXT,
  ring_running BOOLEAN NOT NULL DEFAULT FALSE,
  ring_restart_count INT NOT NULL DEFAULT 0,
  last_ring_exit_code INT,
  last_probe_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recorder_camera_health_connected
  ON recorder_camera_health (connected);

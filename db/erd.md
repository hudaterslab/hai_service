# ERD Summary

## Core Entities
- `cameras`: camera source and stream path.
- `event_policies`: per-camera per-event behavior (`clip` or `snapshot`).
- `camera_rois`: per-camera ROI zones (normalized coordinates).
- `events`: detected event records.
- `ai_detection_logs`: model inference logs per camera.
- `ai_camera_state`: per-camera cooldown state for trigger suppression.
- `artifacts`: generated media from events (clip/snapshot).
- `destinations`: external endpoints (HTTPS POST or SFTP).
- `routing_rules`: mapping from `(camera, event)` to destination.
- `delivery_attempts`: reliable delivery state and retries.
- `app_settings`: global runtime settings (`ai_model` key).

## Relationships
- `cameras (1) -> (N) event_policies`
- `cameras (1) -> (N) events`
- `cameras (1) -> (1) camera_rois`
- `cameras (1) -> (N) ai_detection_logs`
- `cameras (1) -> (1) ai_camera_state`
- `events (1) -> (N) artifacts`
- `artifacts (1) -> (N) delivery_attempts`
- `destinations (1) -> (N) routing_rules`
- `destinations (1) -> (N) delivery_attempts`
- `cameras (1) -> (N) routing_rules`

## Design Notes
- Event mode is selected in `event_policies.mode`.
- AI model path/enabled/timeout is stored in `app_settings.key = ai_model`.
- Worker order is `AI detect -> event create -> artifact -> delivery queue`.
- Both clip and snapshot config columns exist in one row. Only relevant fields are used by mode.
- Delivery uses `at-least-once` semantics via `delivery_attempts`.

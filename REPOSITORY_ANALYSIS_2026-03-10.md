# VMS 8CH WebRTC Repository Analysis

Date: 2026-03-10
Path: `/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc`

## 1. Executive Summary

This repository is a Docker Compose based edge VMS prototype for up to 8 RTSP cameras. It provides:

- camera registration and monitoring
- WebRTC live view through MediaMTX
- event creation from AI/model outputs
- artifact generation as clip or snapshot
- artifact delivery to external HTTP or SFTP destinations
- a browser UI served by the API container

The codebase is not just a skeleton anymore. It contains a working FastAPI backend, a recorder worker, a delivery worker, a SQLite-based dev mode, a browser UI, event-pack logic, and optional DXNN/DXRT integration. It is still clearly an edge prototype rather than a hardened production system.

## 2. Repository Layout

Top-level structure:

- `config/`
  - `vms.example.yaml`: example runtime config
  - `event_packs/edge-basic@1.0.0.json`: rule pack used by recorder event-pack logic
- `db/`
  - `schema.sql`: full bootstrap schema
  - `migrations/`: incremental SQL migrations
  - `erd.md`: brief schema summary
- `deploy/`
  - `.env`, `.env.example`
  - `docker-compose.yml`: production-like stack
  - `docker-compose.dev.yml`: SQLite dev stack
  - `mediamtx.yml`: MediaMTX config
- `models/`
  - `yolo_person_exit_model.py`: Python YOLO-based detection runner
  - `dxnn_helmet_runner.py`: DXNN runner with optional host-forward mode
  - helper/sample model scripts
  - model weights
- `openapi/`
  - `vms-api.yaml`: API draft, not fully aligned with implementation
- `runtime/`
  - runtime persistence root for media and redis
- `scripts/`
  - `linux/`: DXRT/DXNN host-side helpers
  - `windows/`: local dev and remote helper scripts
- `services/`
  - `api/`: FastAPI application and static UI
  - `recorder/`: polling, inference, event creation, artifact generation
  - `delivery/`: outgoing delivery worker

## 3. Runtime Architecture

Production-like stack in `deploy/docker-compose.yml` starts 6 services:

- `postgres`
  - metadata database
  - bootstraps from `db/schema.sql`
- `redis`
  - present in stack, but current Python services do not meaningfully depend on Redis logic yet
- `mediamtx`
  - RTSP ingest and WebRTC endpoint
- `vms-api`
  - FastAPI backend and static frontend
- `event-recorder`
  - camera reachability checks
  - AI/model execution
  - event creation
  - clip/snapshot generation
- `delivery-worker`
  - reliable artifact delivery with retries

Actual runtime flow:

1. Cameras are stored in `cameras`.
2. Recorder probes RTSP connectivity and updates `cameras.status` and `recorder_camera_health`.
3. If camera-level model settings are enabled, recorder runs the configured model script.
4. Model output creates rows in `ai_detection_logs`.
5. Recorder optionally derives events from:
   - event-pack logic
   - model-provided `events`
   - single-trigger fallback mode
6. New rows in `events` are converted into `artifacts`.
7. Matching `routing_rules` create `delivery_attempts`.
8. Delivery worker sends artifacts and updates retry state.

## 4. Configuration Model

### 4.1 YAML config

`config/vms.example.yaml` expresses the intended product-level config surface:

- server name and timezone
- max cameras
- WebRTC and ingest behavior
- storage paths and retention
- example event policies
- delivery destinations and routing

Important detail: the running Python services do not currently parse this YAML deeply for most behavior. It is mounted into containers, but most actual runtime behavior is driven by:

- environment variables
- PostgreSQL rows
- camera-level settings stored through the API

This means the YAML is closer to an intended configuration contract than the main active source of truth.

### 4.2 Environment variables

`deploy/.env` and `.env.example` currently define:

- `VMS_DATA_ROOT`
- `DXRT_HOST_DIR`
- `DXRT_HOST_LIB_DIR`

These are used by Compose for bind mounts. Current checked-in `.env` matches `.env.example`.

### 4.3 Compose-level operational settings

Important operational defaults in `deploy/docker-compose.yml`:

- API auth is disabled by default: `AUTH_ENABLED=false`
- default JWT secret is weak: `JWT_SECRET=change-me`
- default fallback users are present in code and compose env:
  - `admin/admin`
  - `operator/operator`
- DXNN host bridge is configured as required:
  - `DXNN_HOST_INFER_URL=http://host.docker.internal:18081/infer`
  - `DXNN_HOST_REQUIRED=true`
- artifact generation uses ffmpeg:
  - `USE_FFMPEG_ARTIFACTS=true`
- RTSP ring buffer is disabled:
  - `ENABLE_RTSP_RING_BUFFER=false`

## 5. Database Model

Main schema entities from `db/schema.sql`:

- `cameras`
  - source of RTSP stream metadata
- `event_policies`
  - per camera and per event behavior
  - mode is `clip` or `snapshot`
- `camera_rois`
  - ROI/zones per camera
- `app_settings`
  - global settings like `ai_model`, `person_event_rule`, `webrtc`
- `camera_model_settings`
  - per-camera model enablement, path, thresholds, extra JSON
- `camera_event_pack_settings`
  - per-camera pack enablement and pack params
- `events`
  - generated event records
- `artifacts`
  - files generated from events
- `destinations`
  - external delivery endpoints
- `routing_rules`
  - event to destination mappings
- `delivery_attempts`
  - queue/retry state
- `ai_detection_logs`
  - raw model execution logs
- `ai_camera_state`
  - cooldown tracking for trigger suppression
- `recorder_camera_health`
  - camera probe and ring-recorder health

Migration history:

- `0001_init.sql`
  - base schema
- `0002_recorder_camera_health.sql`
  - recorder health table
- `0003_edge_event_pack.sql`
  - camera model settings
  - camera event-pack settings
  - `webrtc` and `person_event_rule` app settings

Schema quality notes:

- enough structure exists for a usable prototype
- several settings are duplicated across YAML, env, and DB
- Redis is not represented as a functional system of record

## 6. API Service

Implementation lives in `services/api/app/main.py`.

### 6.1 Technology

- FastAPI
- psycopg 3
- static frontend served directly by the app
- JWT auth support with role-based checks

### 6.2 Main capabilities

Implemented endpoints cover:

- auth
  - `/auth/login`
  - `/auth/me`
  - `/auth/hash-password`
- health
  - `/healthz`
- camera management
  - CRUD-like endpoints
  - `/cameras/discover`
  - `/cameras/discover/jobs`
  - `/cameras/{id}/snapshot`
- camera ROI
  - get/update
- event policy management
- global settings
  - AI model settings
  - person event rule
  - WebRTC settings
- per-camera model settings
- per-camera event-pack settings
- model discovery
  - `/models/list`
- AI preview/debug endpoint
  - `/dev/ai/preview`
- event-pack listing
- destination management
- routing rule management
- events list/create
- artifacts list/redeliver
- monitoring
  - `/monitor/cameras`

### 6.3 Discovery behavior

Camera discovery has two modes:

- direct RTSP probing over candidate URLs
- optional ONVIF discovery

Async job mode exists for discovery, backed by in-memory process state:

- `DISCOVER_JOBS`
- no persistent job queue
- state is lost on API restart

### 6.4 Auth behavior

Auth is conditional:

- if `AUTH_ENABLED=false`, role checks are effectively bypassed
- if enabled, JWT is used with shared secret
- users come from `AUTH_USERS_JSON`

This is acceptable for lab use, not for production.

### 6.5 UI

Static frontend files exist under `services/api/app/static/`.
The UI includes pages and logic for:

- dashboard/monitoring
- camera management
- live view
- ROI editing
- event policies
- routing
- AI settings
- AI debug
- network/discovery
- auth

The frontend is substantial, not placeholder-only.

## 7. Recorder Service

Implementation lives in `services/recorder/worker.py`.

This is the most important runtime component.

### 7.1 Core responsibilities

- ensure media directories exist
- probe camera RTSP connectivity
- update camera status
- optionally maintain per-camera ring recorders
- execute AI/model runner scripts
- log model outputs
- create events
- build artifacts for events
- enqueue delivery attempts
- prune logs/events if disk is low

### 7.2 Camera connectivity

Connectivity is checked by opening a TCP connection and sending RTSP `OPTIONS`.
Per-camera state includes:

- connected or disconnected
- exponential reconnect backoff
- next retry timestamp

Current implementation limits polling to first 8 enabled cameras:

- `SELECT * FROM cameras WHERE enabled = TRUE ... LIMIT 8`

That matches the product target.

### 7.3 AI execution model

The recorder does not embed model logic directly.
Instead it shells out to a Python runner:

- `.py` model path: run script directly
- non-`.py` model path:
  - `.dxnn` uses `dxnn_helmet_runner.py`
  - other model paths default to `yolo_person_exit_model.py`

The recorder sends a JSON request over stdin and expects JSON on stdout.

This design makes model integration flexible, but also adds operational fragility:

- shell-out overhead
- dependency drift in model scripts
- stdout/stderr parsing failure modes

### 7.4 Event creation sources

Recorder can create events from three sources:

1. Event-pack rules evaluated over model detections
2. Explicit `events` list returned by the model
3. Backward-compatible single `trigger` fallback

This is a useful layered design. It allows:

- simple models to just emit `trigger`
- richer models to emit detections
- recorder to remain responsible for domain rule evaluation

### 7.5 Event-pack logic

Current pack file: `config/event_packs/edge-basic@1.0.0.json`

Implemented event-pack rules:

- `person_cross_roi`
- `helmet_missing_in_roi`
- `vehicle_move_without_signalman`
- `no_parking_stop`

The recorder contains concrete geometric logic for:

- polygon and rectangle ROIs
- bottom-entry checks
- ROI overlap checks
- approximate vehicle stop tracking
- cooldown handling per rule

This is more sophisticated than the README suggests.

### 7.6 Artifact generation

Artifact generation runs on unprocessed events:

- default artifact kind is `snapshot`
- if event policy mode is `clip`, kind becomes `clip`

Generation paths:

- ffmpeg-based real artifacts if enabled
- placeholder file generation fallback if ffmpeg capture fails

For clips:

- if ring buffer is enabled, recorder can assemble pre/post event clips from `.ts` segments
- if ring buffer is disabled or unavailable, clip capture falls back to direct ffmpeg recording

Important limitation:

- direct ffmpeg clip mode does not really implement true pre-event capture
- comment in code explicitly notes pre-event clip is not implemented in the simple mode

### 7.7 Disk management

If disk free ratio falls below threshold, recorder prunes:

- oldest `ai_detection_logs`
- old events with no pending/in-progress/failed delivery dependencies

This is useful but blunt. There is no richer retention policy enforcement yet.

## 8. Delivery Worker

Implementation lives in `services/delivery/worker.py`.

### 8.1 Responsibilities

- poll `delivery_attempts`
- lock one due row with `FOR UPDATE SKIP LOCKED`
- send artifact
- mark success or schedule retry
- optionally delete local artifact after success

### 8.2 Delivery modes

Supported destinations in code:

- `https_post`
- `sftp`

Practical note:

- API validation for destination creation currently only accepts `https_post`
- SFTP still exists in DB schema, config examples, and worker implementation
- this is a clear spec/implementation mismatch

### 8.3 HTTPS mode specifics

Current HTTP delivery expects a special API mode:

- `apiMode = cctv_img_v1`

Behavior:

- snapshot only
- multipart upload
- event type mapped to numeric code
- terminal/cctv IDs taken from destination config

This is not generic webhook delivery. It is a specific integration contract.

### 8.4 Retry semantics

Backoff sequence:

- 5s
- 15s
- 30s
- 60s
- 120s

Semantics are at-least-once delivery, which is appropriate for this domain.

## 9. Models

### 9.1 `yolo_person_exit_model.py`

Despite the name, the current default behavior is "person dwell" rather than exit detection.

Modes in practice:

- if `personEventRule.enabled` is true:
  - trigger when a person remains present for `dwellSec`
- if false:
  - use absence-based exit detection

Outputs:

- trigger flag
- score
- label
- event type/severity
- detections
- payload

This script persists per-camera state to disk under `.runtime`.

### 9.2 `dxnn_helmet_runner.py`

Supports two execution styles:

- call a host inference HTTP service first
- if that is unavailable and not required, run local DXNN inference

It:

- loads model metadata if available
- infers input tensor shape
- captures one frame from RTSP
- preprocesses into model input
- decodes YOLO-like outputs
- derives helmet-missing semantics from person/head/helmet detections

This runner is specialized, not a generic DXNN adapter.

### 9.3 Other model scripts

- `force_trigger_model.py`
  - likely simple testing helper
- `sample_model.py`
  - sample integration stub

These support experimentation more than production use.

## 10. DXRT / DXNN Host Integration

Linux helper scripts:

- `scripts/linux/install_dxrt_host.sh`
  - installs build deps
  - clones/builds DEEPX runtime
- `scripts/linux/install_dxnn_host_service.sh`
  - installs a systemd service exposing host inference on port `18081`
- `scripts/linux/dxnn_host_infer_service.py`
  - HTTP service for host-side DXNN inference

Current handoff status from `HANDOFF_2026-03-10_165.md`:

- stack deployment on `192.168.1.165` is up
- API health is confirmed
- DXRT host inference setup is not completed
- `install_dxrt_host.sh` failed due to missing `ONNXLIB_DIRS`
- `dxrt.service` dependency was not available for the DXNN host service

Implication:

- base VMS runtime is usable
- DXNN-host-based inference path is still a blocker for AI features that require that stack

## 11. Dev Mode

Dev stack in `deploy/docker-compose.dev.yml` uses:

- `vms-api-dev`
- `vms-worker-dev`
- SQLite file at `/app/data/dev.db`

Dev API implementation is in `services/api/app/dev_main.py`.
Dev worker implementation is in `services/recorder/dev_worker.py`.

Dev mode is simpler than production mode:

- SQLite instead of PostgreSQL
- fewer features
- good for local UI/API iteration
- not a faithful operational mirror of production

Notable drift:

- dev mode schema and routes lag production features
- production has auth, delivery nuances, event packs, and monitoring details not fully mirrored in dev

## 12. OpenAPI vs Actual Implementation

`openapi/vms-api.yaml` is an API draft, not a precise contract for the running code.

Observed drift:

- implementation contains more endpoints than the OpenAPI file
- implementation has richer auth and settings behavior
- destination handling is stricter in code than generic schema/docs imply
- monitoring and debug endpoints exist in code but are not fully represented in the draft

Conclusion:

- clients should treat the running FastAPI app as source of truth, not the OpenAPI draft

## 13. Current Operational State

From the checked-in handoff document:

- deployment target: `192.168.1.165`
- compose stack is up
- API health at `http://127.0.0.1:8080/healthz` is confirmed
- containers expected:
  - `vms-api`
  - `vms-event-recorder`
  - `vms-delivery-worker`
  - `vms-mediamtx`
  - `vms-postgres`
  - `vms-redis`

Storage status:

- media and redis data use host path under `runtime/`
- PostgreSQL uses Docker named volume `deploy_vms_pg_data`

Reason:

- the target disk/filesystem did not support permission behavior required by the official Postgres container for bind-mounted data

## 14. Strengths

- architecture is simple and understandable
- API, recorder, and delivery concerns are cleanly separated
- recorder supports both simple trigger and richer detection-driven rule evaluation
- event packs give domain-specific flexibility without hardcoding all behavior into models
- ffmpeg fallback behavior makes the pipeline resilient
- delivery queue uses proper DB locking semantics
- static UI is already substantial enough for operator workflows

## 15. Weaknesses and Risks

### 15.1 Security

- auth disabled by default in compose
- weak default JWT secret
- default plaintext fallback users
- checked-in `deploy/.env`
- likely no TLS termination or reverse-proxy hardening

### 15.2 Configuration sprawl

Behavior is split across:

- YAML
- Compose env vars
- DB settings
- camera-level settings
- model-side env vars

This increases support cost and makes troubleshooting harder.

### 15.3 Redis underused

Redis is deployed but current core Python logic relies mainly on PostgreSQL and in-memory state.
Unless future features need it, Redis is currently extra operational weight.

### 15.4 In-memory job/runtime state

- discovery jobs are in-memory
- recorder runtime state is in-memory
- some model state is local filesystem state

Restarts can lose ephemeral context.

### 15.5 Spec drift

- OpenAPI draft does not match live implementation
- YAML intent does not fully match active runtime behavior
- API allows only `https_post` destination creation even though worker and schema support SFTP

### 15.6 Clip semantics

True pre-event clip generation depends on ring buffer mode.
When ring buffer is disabled, "clip" is effectively post-event direct capture only.

### 15.7 DXRT dependency risk

AI path for DXNN appears operationally fragile and environment-sensitive.
The handoff confirms host-side setup is incomplete.

## 16. Practical Recommendations

### Short term

1. Enable auth in production-like deployments.
2. Replace `JWT_SECRET` and remove default credentials.
3. Decide whether Redis is actually needed.
4. Align destination API behavior with worker capabilities.
5. Document which settings are authoritative:
   - DB
   - env
   - YAML
6. Decide whether clip mode must support real pre-event capture. If yes, enable and harden ring buffer mode.
7. Validate end-to-end event to artifact to delivery using real cameras.

### Medium term

1. Bring OpenAPI in sync with implementation.
2. Move discovery job state out of process memory if long-running jobs matter.
3. Add structured logging and explicit observability for recorder and delivery state.
4. Reduce duplicated model and event-rule logic between recorder and host inference service.
5. Separate prototype-specific integrations from generic delivery abstractions.

## 17. Bottom Line

This repository is a functional edge VMS prototype with real implementation depth. The core pipeline is:

- register camera
- detect with model
- create event
- generate artifact
- deliver artifact

The strongest part of the system is the recorder and event-pack logic. The weakest parts are operational hardening, configuration consistency, and DXRT host inference reliability.

For a lab or pilot deployment, the repository is usable. For production deployment, security, configuration discipline, API/spec alignment, and AI runtime hardening need more work.

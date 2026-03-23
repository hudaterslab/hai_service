import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "dev.db"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Edge Console Control API (SQLite Dev)", version="0.1.0-dev")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS cameras (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              rtsp_url TEXT NOT NULL,
              onvif_profile TEXT,
              webrtc_path TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'offline',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_policies (
              id TEXT PRIMARY KEY,
              camera_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              mode TEXT NOT NULL,
              clip_pre_sec INTEGER NOT NULL DEFAULT 10,
              clip_post_sec INTEGER NOT NULL DEFAULT 20,
              clip_cooldown_sec INTEGER NOT NULL DEFAULT 5,
              clip_merge_window_sec INTEGER NOT NULL DEFAULT 3,
              snapshot_count INTEGER NOT NULL DEFAULT 1,
              snapshot_interval_ms INTEGER NOT NULL DEFAULT 0,
              snapshot_format TEXT NOT NULL DEFAULT 'jpg',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (camera_id, event_type)
            );

            CREATE TABLE IF NOT EXISTS destinations (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              type TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              config_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS routing_rules (
              id TEXT PRIMARY KEY,
              camera_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              artifact_kind TEXT NOT NULL DEFAULT 'both',
              destination_id TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              camera_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              severity TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS camera_rois (
              camera_id TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL DEFAULT 0,
              zones_json TEXT NOT NULL DEFAULT '[]',
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY,
              event_id TEXT NOT NULL,
              camera_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              local_path TEXT NOT NULL,
              uri TEXT,
              mime_type TEXT NOT NULL,
              checksum_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        cur = c.execute("SELECT key FROM app_settings WHERE key = 'ai_model'")
        if not cur.fetchone():
            c.execute(
                "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (
                    "ai_model",
                    json.dumps(
                        {
                            "enabled": False,
                            "modelPath": "",
                            "timeoutSec": 5,
                            "pollSec": 2,
                            "cooldownSec": 10,
                        }
                    ),
                    now_iso(),
                ),
            )
        c.commit()


class CreateCameraRequest(BaseModel):
    name: str
    rtspUrl: str
    webrtcPath: str
    onvifProfile: Optional[str] = None
    enabled: bool = True


class UpdateCameraRequest(BaseModel):
    name: Optional[str] = None
    rtspUrl: Optional[str] = None
    webrtcPath: Optional[str] = None
    onvifProfile: Optional[str] = None
    enabled: Optional[bool] = None


class ClipConfig(BaseModel):
    preSec: int = 10
    postSec: int = 20
    cooldownSec: int = 5
    mergeWindowSec: int = 3


class SnapshotConfig(BaseModel):
    snapshotCount: int = 1
    intervalMs: int = 0
    format: str = "jpg"


class UpsertEventPolicyRequest(BaseModel):
    eventType: str
    mode: str = Field(pattern="^(clip|snapshot)$")
    clip: Optional[ClipConfig] = None
    snapshot: Optional[SnapshotConfig] = None


class CreateDestinationRequest(BaseModel):
    name: str
    type: str = Field(pattern="^(https_post|sftp)$")
    enabled: bool = True
    config: dict[str, Any]


class CreateRoutingRuleRequest(BaseModel):
    cameraId: str
    eventType: str
    artifactKind: str = Field(default="both", pattern="^(clip|snapshot|both)$")
    destinationId: str
    enabled: bool = True


class CreateEventRequest(BaseModel):
    cameraId: str
    type: str = "motion"
    severity: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)


class AIModelSettings(BaseModel):
    enabled: bool = False
    modelPath: str = ""
    timeoutSec: int = 5
    pollSec: int = 2
    cooldownSec: int = 10


class CameraROIRequest(BaseModel):
    enabled: bool = False
    zones: list[dict[str, Any]] = Field(default_factory=list)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True, "db": str(DB_PATH)}


@app.get("/cameras")
def list_cameras():
    with conn() as c:
        rows = c.execute("SELECT * FROM cameras ORDER BY created_at DESC").fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "rtspUrl": r["rtsp_url"],
            "onvifProfile": r["onvif_profile"],
            "webrtcPath": r["webrtc_path"],
            "enabled": bool(r["enabled"]),
            "status": r["status"],
        }
        for r in rows
    ]


@app.post("/cameras", status_code=201)
def create_camera(body: CreateCameraRequest):
    cid = str(uuid.uuid4())
    with conn() as c:
        c.execute(
            """
            INSERT INTO cameras (id, name, rtsp_url, onvif_profile, webrtc_path, enabled, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'offline', ?, ?)
            """,
            (cid, body.name, body.rtspUrl, body.onvifProfile, body.webrtcPath, 1 if body.enabled else 0, now_iso(), now_iso()),
        )
        c.commit()
    return {
        "id": cid,
        "name": body.name,
        "rtspUrl": body.rtspUrl,
        "onvifProfile": body.onvifProfile,
        "webrtcPath": body.webrtcPath,
        "enabled": body.enabled,
        "status": "offline",
    }


@app.patch("/cameras/{camera_id}")
def patch_camera(camera_id: str, body: UpdateCameraRequest):
    updates = []
    vals: list[Any] = []
    mapping = {
        "name": body.name,
        "rtsp_url": body.rtspUrl,
        "webrtc_path": body.webrtcPath,
        "onvif_profile": body.onvifProfile,
    }
    for k, v in mapping.items():
        if v is not None:
            updates.append(f"{k} = ?")
            vals.append(v)
    if body.enabled is not None:
        updates.append("enabled = ?")
        vals.append(1 if body.enabled else 0)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    vals.extend([now_iso(), camera_id])
    with conn() as c:
        cur = c.execute(f"UPDATE cameras SET {', '.join(updates)}, updated_at = ? WHERE id = ?", vals)
        c.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="camera not found")
        row = c.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return {
        "id": row["id"],
        "name": row["name"],
        "rtspUrl": row["rtsp_url"],
        "onvifProfile": row["onvif_profile"],
        "webrtcPath": row["webrtc_path"],
        "enabled": bool(row["enabled"]),
        "status": row["status"],
    }


@app.patch("/cameras/{camera_id}/event-policy")
def upsert_event_policy(camera_id: str, body: UpsertEventPolicyRequest):
    clip = body.clip or ClipConfig()
    snap = body.snapshot or SnapshotConfig()
    with conn() as c:
        c.execute(
            """
            INSERT INTO event_policies (
              id, camera_id, event_type, mode, clip_pre_sec, clip_post_sec, clip_cooldown_sec, clip_merge_window_sec,
              snapshot_count, snapshot_interval_ms, snapshot_format, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id, event_type) DO UPDATE SET
              mode=excluded.mode,
              clip_pre_sec=excluded.clip_pre_sec,
              clip_post_sec=excluded.clip_post_sec,
              clip_cooldown_sec=excluded.clip_cooldown_sec,
              clip_merge_window_sec=excluded.clip_merge_window_sec,
              snapshot_count=excluded.snapshot_count,
              snapshot_interval_ms=excluded.snapshot_interval_ms,
              snapshot_format=excluded.snapshot_format,
              updated_at=excluded.updated_at
            """,
            (
                str(uuid.uuid4()),
                camera_id,
                body.eventType,
                body.mode,
                clip.preSec,
                clip.postSec,
                clip.cooldownSec,
                clip.mergeWindowSec,
                snap.snapshotCount,
                snap.intervalMs,
                snap.format,
                now_iso(),
                now_iso(),
            ),
        )
        c.commit()
    return {
        "cameraId": camera_id,
        "eventType": body.eventType,
        "mode": body.mode,
        "clip": clip.model_dump(),
        "snapshot": snap.model_dump(),
    }


@app.get("/cameras/{camera_id}/roi")
def get_camera_roi(camera_id: str):
    with conn() as c:
        row = c.execute("SELECT * FROM camera_rois WHERE camera_id = ?", (camera_id,)).fetchone()
    if not row:
        return {"cameraId": camera_id, "enabled": False, "zones": []}
    return {
        "cameraId": row["camera_id"],
        "enabled": bool(row["enabled"]),
        "zones": json.loads(row["zones_json"]),
    }


@app.put("/cameras/{camera_id}/roi")
def put_camera_roi(camera_id: str, body: CameraROIRequest):
    with conn() as c:
        c.execute(
            """
            INSERT INTO camera_rois (camera_id, enabled, zones_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(camera_id) DO UPDATE SET
              enabled=excluded.enabled,
              zones_json=excluded.zones_json,
              updated_at=excluded.updated_at
            """,
            (camera_id, 1 if body.enabled else 0, json.dumps(body.zones), now_iso()),
        )
        c.commit()
    return {"cameraId": camera_id, "enabled": body.enabled, "zones": body.zones}


@app.get("/event-policies")
def list_event_policies():
    with conn() as c:
        rows = c.execute("SELECT * FROM event_policies ORDER BY updated_at DESC").fetchall()
    return [
        {
            "cameraId": r["camera_id"],
            "eventType": r["event_type"],
            "mode": r["mode"],
            "clip": {
                "preSec": r["clip_pre_sec"],
                "postSec": r["clip_post_sec"],
                "cooldownSec": r["clip_cooldown_sec"],
                "mergeWindowSec": r["clip_merge_window_sec"],
            },
            "snapshot": {
                "snapshotCount": r["snapshot_count"],
                "intervalMs": r["snapshot_interval_ms"],
                "format": r["snapshot_format"],
            },
        }
        for r in rows
    ]


@app.get("/settings/ai-model")
def get_ai_model():
    with conn() as c:
        row = c.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'").fetchone()
    cfg = json.loads(row["value_json"]) if row else {}
    return AIModelSettings(**cfg).model_dump()


@app.put("/settings/ai-model")
def put_ai_model(body: AIModelSettings):
    with conn() as c:
        c.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES ('ai_model', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (json.dumps(body.model_dump()), now_iso()),
        )
        c.commit()
    return body.model_dump()


@app.get("/destinations")
def list_destinations():
    with conn() as c:
        rows = c.execute("SELECT * FROM destinations ORDER BY created_at DESC").fetchall()
    return [
        {"id": r["id"], "name": r["name"], "type": r["type"], "enabled": bool(r["enabled"]), "config": json.loads(r["config_json"])}
        for r in rows
    ]


@app.post("/destinations", status_code=201)
def create_destination(body: CreateDestinationRequest):
    did = str(uuid.uuid4())
    with conn() as c:
        c.execute(
            """
            INSERT INTO destinations (id, name, type, enabled, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (did, body.name, body.type, 1 if body.enabled else 0, json.dumps(body.config), now_iso(), now_iso()),
        )
        c.commit()
    return {"id": did, "name": body.name, "type": body.type, "enabled": body.enabled, "config": body.config}


@app.get("/routing-rules")
def list_routes():
    with conn() as c:
        rows = c.execute("SELECT * FROM routing_rules ORDER BY created_at DESC").fetchall()
    return [
        {
            "id": r["id"],
            "cameraId": r["camera_id"],
            "eventType": r["event_type"],
            "artifactKind": r["artifact_kind"],
            "destinationId": r["destination_id"],
            "enabled": bool(r["enabled"]),
        }
        for r in rows
    ]


@app.post("/routing-rules", status_code=201)
def create_route(body: CreateRoutingRuleRequest):
    rid = str(uuid.uuid4())
    with conn() as c:
        c.execute(
            """
            INSERT INTO routing_rules (id, camera_id, event_type, artifact_kind, destination_id, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (rid, body.cameraId, body.eventType, body.artifactKind, body.destinationId, 1 if body.enabled else 0, now_iso(), now_iso()),
        )
        c.commit()
    return {
        "id": rid,
        "cameraId": body.cameraId,
        "eventType": body.eventType,
        "artifactKind": body.artifactKind,
        "destinationId": body.destinationId,
        "enabled": body.enabled,
    }


@app.get("/events")
def list_events(cameraId: Optional[str] = None, type: Optional[str] = None):
    q = "SELECT * FROM events"
    vals: list[Any] = []
    clauses = []
    if cameraId:
        clauses.append("camera_id = ?")
        vals.append(cameraId)
    if type:
        clauses.append("event_type = ?")
        vals.append(type)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY occurred_at DESC LIMIT 200"
    with conn() as c:
        rows = c.execute(q, vals).fetchall()
    return [
        {
            "id": r["id"],
            "cameraId": r["camera_id"],
            "type": r["event_type"],
            "severity": r["severity"],
            "occurredAt": r["occurred_at"],
            "payload": json.loads(r["payload_json"]),
        }
        for r in rows
    ]


@app.post("/events", status_code=201)
def create_event(body: CreateEventRequest):
    eid = str(uuid.uuid4())
    with conn() as c:
        c.execute(
            """
            INSERT INTO events (id, camera_id, event_type, severity, occurred_at, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (eid, body.cameraId, body.type, body.severity, now_iso(), json.dumps(body.payload), now_iso()),
        )
        c.commit()
    return {
        "id": eid,
        "cameraId": body.cameraId,
        "type": body.type,
        "severity": body.severity,
        "occurredAt": now_iso(),
        "payload": body.payload,
    }


@app.get("/artifacts")
def list_artifacts():
    with conn() as c:
        rows = c.execute("SELECT * FROM artifacts ORDER BY created_at DESC LIMIT 200").fetchall()
    return [
        {
            "id": r["id"],
            "eventId": r["event_id"],
            "cameraId": r["camera_id"],
            "kind": r["kind"],
            "uri": r["uri"],
            "mimeType": r["mime_type"],
            "checksumSha256": r["checksum_sha256"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


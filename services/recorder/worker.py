import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from recorder.app.artifacts import ArtifactBuilder, RingBufferManager, normalize_rotate_deg, parse_dt
from recorder.app.config import RecorderSettings


DATABASE_URL = os.getenv("DATABASE_URL", "postgres://vms:vms@postgres:5432/vms?sslmode=disable")
DEFAULT_BASE_DIR = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path("/app")
BASE_DIR = Path(os.getenv("PROJECT_ROOT", str(DEFAULT_BASE_DIR)))
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "runtime" / "media")))
EVENT_PACKS_DIR = Path(os.getenv("EVENT_PACKS_DIR", str(BASE_DIR / "config" / "event_packs")))
MODEL_PYTHON_BIN = os.getenv("MODEL_PYTHON_BIN", "python")
CAMERA_CONNECT_TIMEOUT_SEC = float(os.getenv("CAMERA_CONNECT_TIMEOUT_SEC", "2.5"))
CAMERA_RECONNECT_MAX_BACKOFF_SEC = int(os.getenv("CAMERA_RECONNECT_MAX_BACKOFF_SEC", "60"))
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
USE_FFMPEG_ARTIFACTS = os.getenv("USE_FFMPEG_ARTIFACTS", "false").lower() == "true"
ENABLE_RTSP_RING_BUFFER = os.getenv("ENABLE_RTSP_RING_BUFFER", "false").lower() == "true"
RING_SEGMENT_SEC = int(os.getenv("RING_SEGMENT_SEC", "1"))
RING_BUFFER_SECONDS = int(os.getenv("RING_BUFFER_SECONDS", "120"))
DB_CONNECT_MAX_RETRIES = int(os.getenv("DB_CONNECT_MAX_RETRIES", "20"))
DB_CONNECT_RETRY_BASE_SEC = float(os.getenv("DB_CONNECT_RETRY_BASE_SEC", "0.5"))
DB_CONNECT_RETRY_MAX_SEC = float(os.getenv("DB_CONNECT_RETRY_MAX_SEC", "3.0"))
LOW_DISK_FREE_RATIO = float(os.getenv("LOW_DISK_FREE_RATIO", "0.10"))
LOG_PRUNE_BATCH_SIZE = int(os.getenv("LOG_PRUNE_BATCH_SIZE", "5000"))
LOG_PRUNE_MAX_BATCHES = int(os.getenv("LOG_PRUNE_MAX_BATCHES", "20"))
EVENT_PRUNE_MIN_AGE_SEC = int(os.getenv("EVENT_PRUNE_MIN_AGE_SEC", "86400"))
DELIVERY_DEDUP_WINDOW_SEC = max(int(os.getenv("DELIVERY_DEDUP_WINDOW_SEC", "10")), 0)
KST = timezone(timedelta(hours=9))
RECORDER_SETTINGS = RecorderSettings.from_env()
ARTIFACT_BUILDER = ArtifactBuilder(RECORDER_SETTINGS)


def _env_path_list(name: str) -> list[Path]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]


def _default_model_roots() -> list[Path]:
    return [BASE_DIR / "models", BASE_DIR]


def _runner_candidates(env_name: str, bundled_name: str) -> list[Path]:
    candidates: list[Path] = []
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        candidates.append(Path(env_value).expanduser())
    for root in (_env_path_list("MODEL_SEARCH_ROOTS") or _default_model_roots()):
        candidates.append(root / bundled_name)
    return candidates


def _event_timezone():
    tz_name = os.getenv("VMS_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return KST


def _now_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone(_event_timezone()).isoformat(timespec="milliseconds")


RING_BUFFER_MANAGER = RingBufferManager(RECORDER_SETTINGS)

CAMERA_RUNTIME_STATE: dict[str, dict] = {}
CAMERA_EVENT_STATE: dict[str, dict] = {}
DEVICE_NAME = RECORDER_SETTINGS.device_name
EVENT_LOG_PATH = Path(os.getenv("EVENT_LOG_PATH", str(MEDIA_ROOT / "logs" / "events.jsonl"))).expanduser()


def append_event_log(
    *,
    event_id,
    camera_id,
    event_type: str,
    severity: str,
    occurred_at,
    payload: dict | None,
    source: str,
) -> None:
    entry = {
        "loggedAt": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "eventId": str(event_id),
        "cameraId": str(camera_id),
        "type": event_type,
        "severity": severity,
        "occurredAt": occurred_at.astimezone(timezone.utc).isoformat() if isinstance(occurred_at, datetime) else None,
        "payload": payload or {},
    }
    try:
        EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def db_conn():
    retries = max(DB_CONNECT_MAX_RETRIES, 1)
    base = max(DB_CONNECT_RETRY_BASE_SEC, 0.1)
    cap = max(DB_CONNECT_RETRY_MAX_SEC, base)
    last_ex: Exception | None = None
    for attempt in range(retries):
        try:
            return psycopg.connect(DATABASE_URL, row_factory=dict_row)
        except psycopg.OperationalError as ex:
            last_ex = ex
            if attempt >= retries - 1:
                break
            sleep_sec = min(base * (2 ** attempt), cap)
            time.sleep(sleep_sec)
    raise last_ex or RuntimeError("db connection failed")


def ensure_dirs():
    ARTIFACT_BUILDER.ensure_dirs()


def disk_free_ratio(path: Path) -> float:
    try:
        st = os.statvfs(str(path))
        total = float(st.f_blocks * st.f_frsize)
        avail = float(st.f_bavail * st.f_frsize)
        if total <= 0:
            return 1.0
        return max(0.0, min(1.0, avail / total))
    except Exception:
        return 1.0


def prune_oldest_logs_if_low_disk():
    if LOW_DISK_FREE_RATIO <= 0:
        return
    if disk_free_ratio(Path("/")) >= LOW_DISK_FREE_RATIO:
        return

    batch = max(LOG_PRUNE_BATCH_SIZE, 100)
    max_batches = max(LOG_PRUNE_MAX_BATCHES, 1)
    with db_conn() as conn, conn.cursor() as cur:
        for _ in range(max_batches):
            if disk_free_ratio(Path("/")) >= LOW_DISK_FREE_RATIO:
                break

            cur.execute(
                """
                DELETE FROM ai_detection_logs
                WHERE id IN (
                  SELECT id FROM ai_detection_logs
                  ORDER BY created_at ASC
                  LIMIT %s
                )
                """,
                (batch,),
            )
            deleted_ai = int(cur.rowcount or 0)

            cur.execute(
                """
                DELETE FROM events e
                WHERE e.id IN (
                  SELECT e0.id
                  FROM events e0
                  LEFT JOIN artifacts a ON a.event_id = e0.id
                  LEFT JOIN delivery_attempts da
                    ON da.artifact_id = a.id
                   AND da.status IN ('queued', 'in_progress', 'failed')
                  WHERE e0.occurred_at < NOW() - (%s || ' seconds')::interval
                    AND da.id IS NULL
                  ORDER BY e0.occurred_at ASC
                  LIMIT %s
                )
                """,
                (max(EVENT_PRUNE_MIN_AGE_SEC, 0), batch),
            )
            deleted_events = int(cur.rowcount or 0)

            conn.commit()
            if deleted_ai == 0 and deleted_events == 0:
                break


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_transfer_label(device_name: str, camera_name: str, event_name: str, occurred_at) -> str:
    return ARTIFACT_BUILDER.build_transfer_label(camera_name, event_name, occurred_at)


def build_artifact_stem(device_name: str, camera_name: str, event_name: str, occurred_at) -> str:
    return ARTIFACT_BUILDER.build_artifact_stem(camera_name, event_name, occurred_at)


def make_artifact_file_placeholder(kind: str, camera_id: str, event_id: str, artifact_stem: str | None = None) -> tuple[Path, str]:
    return ARTIFACT_BUILDER.make_placeholder(kind, camera_id, event_id, artifact_stem=artifact_stem)


def make_artifact_file_ffmpeg(
    kind: str,
    camera_id: str,
    event_id: str,
    rtsp_url: str,
    clip_duration_sec: int,
    rotate_deg: int = 0,
    artifact_stem: str | None = None,
) -> tuple[Path | None, str | None]:
    return ARTIFACT_BUILDER.make_ffmpeg_artifact(kind, camera_id, event_id, rtsp_url, clip_duration_sec, rotate_deg=rotate_deg, artifact_stem=artifact_stem)


def make_artifact_file(
    kind: str,
    camera_id: str,
    event_id: str,
    rtsp_url: str,
    clip_duration_sec: int,
    rotate_deg: int = 0,
    artifact_stem: str | None = None,
) -> tuple[Path, str]:
    return ARTIFACT_BUILDER.make_artifact(kind, camera_id, event_id, rtsp_url, clip_duration_sec, rotate_deg=rotate_deg, artifact_stem=artifact_stem)


def ring_dir(camera_id: str) -> Path:
    return RING_BUFFER_MANAGER.ring_dir(camera_id)


def cleanup_old_ring_segments(camera_id: str):
    RING_BUFFER_MANAGER.cleanup_old_segments(camera_id)


def ensure_ring_recorder(camera: dict):
    RING_BUFFER_MANAGER.ensure_recorder(camera)


def stop_ring_recorder(camera_id: str):
    RING_BUFFER_MANAGER.stop_recorder(camera_id)


def parse_segment_time_from_name(p: Path) -> datetime | None:
    return RING_BUFFER_MANAGER.parse_segment_time(p)


def build_clip_from_ring(camera_id: str, event_id: str, start_ts: datetime, end_ts: datetime) -> Path | None:
    return RING_BUFFER_MANAGER.build_clip(camera_id, event_id, start_ts, end_ts)


def get_ai_model_settings(cur) -> dict:
    cur.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'")
    row = cur.fetchone()
    if not row:
        return {"enabled": False, "modelPath": "", "timeoutSec": 5, "pollSec": 2, "cooldownSec": 10}
    cfg = row["value_json"] or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "modelPath": str(cfg.get("modelPath", "")),
        "timeoutSec": max(int(cfg.get("timeoutSec", 5)), 1),
        "pollSec": max(int(cfg.get("pollSec", 2)), 1),
        "cooldownSec": max(int(cfg.get("cooldownSec", 10)), 0),
    }


def get_person_event_rule(cur) -> dict:
    cur.execute("SELECT value_json FROM app_settings WHERE key = 'person_event_rule'")
    row = cur.fetchone()
    if not row:
        return {"enabled": True, "dwellSec": 5, "cooldownSec": 10, "eventType": "person_detected", "severity": "high"}
    cfg = row["value_json"] or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "dwellSec": max(int(cfg.get("dwellSec", 5)), 1),
        "cooldownSec": max(int(cfg.get("cooldownSec", 10)), 0),
        "eventType": str(cfg.get("eventType", "person_detected")),
        "severity": str(cfg.get("severity", "high")),
    }


def get_camera_model_settings(cur, camera_id: str, global_cfg: dict) -> dict:
    cur.execute("SELECT * FROM camera_model_settings WHERE camera_id = %s", (camera_id,))
    row = cur.fetchone()
    if not row:
        return dict(global_cfg)
    return {
        "enabled": bool(row["enabled"]),
        "modelPath": str(row["model_path"] or ""),
        "timeoutSec": max(int(row["timeout_sec"] or 5), 1),
        "pollSec": max(int(row["poll_sec"] or 2), 1),
        "cooldownSec": max(int(row["cooldown_sec"] or 10), 0),
        "confidenceThreshold": float(row["confidence_threshold"] or 0.35),
        "extra": row["extra_json"] or {},
    }


def get_camera_event_pack_settings(cur, camera_id: str) -> dict:
    cur.execute("SELECT * FROM camera_event_pack_settings WHERE camera_id = %s", (camera_id,))
    row = cur.fetchone()
    if not row:
        return {"enabled": False, "packId": "edge-basic", "packVersion": "1.0.0", "params": {}}
    return {
        "enabled": bool(row["enabled"]),
        "packId": str(row["pack_id"] or "edge-basic"),
        "packVersion": str(row["pack_version"] or "1.0.0"),
        "params": row["params_json"] or {},
    }


def list_event_packs() -> list[dict]:
    out: list[dict] = []
    if not EVENT_PACKS_DIR.exists():
        return out
    for p in sorted(EVENT_PACKS_DIR.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        if not raw.get("packId") or not raw.get("version") or not isinstance(raw.get("events"), list):
            continue
        out.append(raw)
    return out


def get_event_pack(pack_id: str, version: str) -> dict | None:
    matches = [p for p in list_event_packs() if str(p.get("packId")) == pack_id]
    if not matches:
        return None
    for m in matches:
        if str(m.get("version")) == version:
            return m
    return None


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def center_of_detection(d: dict) -> tuple[float, float]:
    if all(k in d for k in ("nx", "ny", "nw", "nh")):
        return (
            clamp01(float(d.get("nx", 0.0)) + float(d.get("nw", 0.0)) * 0.5),
            clamp01(float(d.get("ny", 0.0)) + float(d.get("nh", 0.0)) * 0.5),
        )
    if all(k in d for k in ("x", "y", "w", "h")):
        return (
            clamp01(float(d.get("x", 0.0)) + float(d.get("w", 0.0)) * 0.5),
            clamp01(float(d.get("y", 0.0)) + float(d.get("h", 0.0)) * 0.5),
        )
    return (clamp01(float(d.get("cx", 0.0))), clamp01(float(d.get("cy", 0.0))))


def point_in_polygon(px: float, py: float, points: list[dict]) -> bool:
    if len(points) < 3:
        return False
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi = float(points[i].get("x", 0.0))
        yi = float(points[i].get("y", 0.0))
        xj = float(points[j].get("x", 0.0))
        yj = float(points[j].get("y", 0.0))
        cross = ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) if (yj - yi) else 1e-9) + xi)
        if cross:
            inside = not inside
        j = i
    return inside


def zone_contains(zone: dict, px: float, py: float) -> bool:
    if str(zone.get("shape", "rect")).lower() == "polygon":
        points = zone.get("points") or []
        if not isinstance(points, list):
            return False
        return point_in_polygon(px, py, points)
    x = float(zone.get("x", 0.0))
    y = float(zone.get("y", 0.0))
    w = float(zone.get("w", 0.0))
    h = float(zone.get("h", 0.0))
    return x <= px <= (x + w) and y <= py <= (y + h)


def find_zone(roi: dict, name: str) -> dict | None:
    zones = roi.get("zones") or []
    for z in zones:
        if str(z.get("name", "")).strip() == name:
            return z
    return zones[0] if zones else None


def label_is_person(label: str) -> bool:
    l = label.lower().strip()
    return l in {"person", "signalman", "worker", "human"}


def label_is_helmet(label: str) -> bool:
    l = label.lower().strip()
    return l in {"helmet", "hardhat", "safety_helmet"}


def label_is_head(label: str) -> bool:
    l = label.lower().strip()
    return l in {"head", "bare_head", "no_helmet_head", "helmetless_head"}


def label_is_vehicle(label: str) -> bool:
    l = label.lower().strip()
    return l in {"vehicle", "car", "truck", "bus", "forklift"}


def select_detections(model_out: dict, label_fn) -> list[dict]:
    detections = model_out.get("detections") or []
    if not isinstance(detections, list):
        return []
    out = []
    for d in detections:
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", ""))
        if label_fn(label):
            out.append(d)
    return out

def get_camera_state(camera_id: str) -> dict:
    st = CAMERA_RUNTIME_STATE.get(camera_id)
    if st is None:
        st = {"connected": False, "fail_count": 0, "next_retry_ts": 0.0}
        CAMERA_RUNTIME_STATE[camera_id] = st
    return st


def parse_rtsp_host_port(rtsp_url: str) -> tuple[str | None, int | None]:
    try:
        u = urlparse(rtsp_url)
        if u.scheme.lower() != "rtsp":
            return None, None
        return u.hostname, u.port or 554
    except Exception:
        return None, None


def probe_rtsp(rtsp_url: str, timeout_sec: float) -> tuple[bool, str]:
    host, port = parse_rtsp_host_port(rtsp_url)
    if not host or not port:
        return False, "invalid_rtsp_url"
    try:
        with socket.create_connection((host, port), timeout=timeout_sec) as s:
            s.settimeout(timeout_sec)
            req = f"OPTIONS {rtsp_url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: vms-recorder\r\n\r\n"
            s.sendall(req.encode("utf-8"))
            resp = s.recv(512).decode("utf-8", errors="ignore")
            if "RTSP/1.0" in resp:
                return True, "ok"
            return True, "tcp_connected_no_rtsp_header"
    except Exception as ex:
        return False, str(ex)


def set_camera_status(cur, camera_id: str, status: str):
    cur.execute(
        "UPDATE cameras SET status = %s, updated_at = NOW() WHERE id = %s",
        (status, camera_id),
    )


def backoff_seconds(fail_count: int) -> int:
    # 1,2,4,8,... up to CAMERA_RECONNECT_MAX_BACKOFF_SEC
    n = max(fail_count - 1, 0)
    return min(2**n, CAMERA_RECONNECT_MAX_BACKOFF_SEC)


def ensure_camera_connected(cur, camera: dict) -> tuple[bool, str]:
    cam_id = str(camera["id"])
    rtsp_url = camera["rtsp_url"]
    state = get_camera_state(cam_id)
    now = time.time()

    if not state["connected"] and now < state["next_retry_ts"]:
        wait_sec = int(state["next_retry_ts"] - now)
        return False, f"reconnect_backoff_wait:{wait_sec}s"

    ok, reason = probe_rtsp(rtsp_url, CAMERA_CONNECT_TIMEOUT_SEC)
    if ok:
        state["connected"] = True
        state["fail_count"] = 0
        state["next_retry_ts"] = 0.0
        set_camera_status(cur, cam_id, "online")
        return True, "connected"

    state["connected"] = False
    state["fail_count"] += 1
    delay = backoff_seconds(state["fail_count"])
    state["next_retry_ts"] = now + delay
    set_camera_status(cur, cam_id, "offline")
    return False, f"connect_failed:{reason}"


def get_camera_roi(cur, camera_id: str) -> dict:
    cur.execute("SELECT enabled, zones_json FROM camera_rois WHERE camera_id = %s", (camera_id,))
    row = cur.fetchone()
    if not row:
        return {"enabled": False, "zones": []}
    return {
        "enabled": bool(row["enabled"]),
        "zones": row["zones_json"] or [],
    }


def run_ai_model_for_camera(cfg: dict, camera: dict, roi: dict, person_event_rule: dict) -> tuple[bool, dict]:
    model_path = cfg["modelPath"].strip()
    if not model_path:
        return False, {"reason": "ai_model_path_empty"}
    path = Path(model_path)
    if not path.exists():
        return False, {"reason": "ai_model_path_not_found", "modelPath": model_path}

    model_ext = path.suffix.lower()
    if model_ext == ".dxnn":
        runner_candidates = _runner_candidates("DEFAULT_DXNN_MODEL_RUNNER", "dxnn_helmet_runner.py")
    else:
        runner_candidates = _runner_candidates("DEFAULT_AI_MODEL_RUNNER", "yolo_person_exit_model.py")
    runner_path = next((p for p in runner_candidates if p.exists()), None)
    run_env = os.environ.copy()
    if model_ext == ".py":
        cmd = [MODEL_PYTHON_BIN, model_path]
    else:
        if runner_path is None:
            return False, {
                "reason": "ai_model_runner_not_found",
                "runnerPath": "",
                "modelPath": model_path,
            }
        cmd = [MODEL_PYTHON_BIN, str(runner_path)]
        if model_ext == ".dxnn":
            run_env["DXNN_MODEL_PATH"] = model_path
        else:
            run_env["YOLO_MODEL_PATH"] = model_path
            run_env.setdefault("YOLO_CONFIG_DIR", str(MEDIA_ROOT / ".ultralytics"))
            Path(run_env["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
    req = {
        "cameraId": str(camera["id"]),
        "cameraName": camera["name"],
        "eventType": "motion",
        "rtspUrl": camera["rtsp_url"],
        "webrtcPath": camera["webrtc_path"],
        "timestamp": _now_local_iso(),
        "roi": roi,
        "personEventRule": person_event_rule,
        "confidenceThreshold": float(cfg.get("confidenceThreshold", 0.35)),
        "modelPath": model_path,
        "extra": cfg.get("extra", {}) if isinstance(cfg.get("extra"), dict) else {},
    }
    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(req),
            capture_output=True,
            text=True,
            timeout=cfg["timeoutSec"],
            check=False,
            env=run_env,
        )
    except Exception as ex:
        return False, {"reason": "execute_failed", "error": str(ex), "cmd": cmd}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    detail = {"cmd": cmd, "exitCode": proc.returncode, "stdout": stdout[:500], "stderr": stderr[:500]}
    if proc.returncode != 0:
        return False, {**detail, "reason": "non_zero_exit"}
    if not stdout:
        return False, {**detail, "reason": "empty_stdout"}

    try:
        out = json.loads(stdout)
        trigger = bool(out.get("trigger", False))
        detail["modelOutput"] = {
            "trigger": trigger,
            "score": out.get("score"),
            "label": out.get("label"),
            "eventType": out.get("eventType", "motion"),
            "severity": out.get("severity", "medium"),
            "payload": out.get("payload", {}),
            "detections": out.get("detections", []),
            "events": out.get("events", []),
        }
        return trigger, detail
    except Exception:
        s = stdout.lower()
        trigger = s in ("1", "true", "yes", "trigger", "pass")
        detail["modelOutputRaw"] = stdout[:200]
        return trigger, detail


def should_trigger(cur, camera_id: str, cooldown_sec: int) -> bool:
    if cooldown_sec <= 0:
        return True
    cur.execute("SELECT last_triggered_at FROM ai_camera_state WHERE camera_id = %s", (camera_id,))
    row = cur.fetchone()
    if not row or not row["last_triggered_at"]:
        return True
    cur.execute(
        "SELECT NOW() >= (%s::timestamptz + (%s || ' seconds')::interval) AS ok",
        (row["last_triggered_at"], cooldown_sec),
    )
    chk = cur.fetchone()
    return bool(chk["ok"])


def event_policy_allows(cur, camera_id: str, event_type: str) -> bool:
    et = str(event_type or "").strip() or "motion"
    cur.execute(
        """
        SELECT 1
        FROM event_policies
        WHERE camera_id = %s
          AND (event_type = %s OR event_type = '*' OR event_type = 'all')
        LIMIT 1
        """,
        (camera_id, et),
    )
    return cur.fetchone() is not None


def mark_triggered(cur, camera_id: str):
    cur.execute(
        """
        INSERT INTO ai_camera_state (camera_id, last_triggered_at)
        VALUES (%s, NOW())
        ON CONFLICT (camera_id) DO UPDATE SET last_triggered_at = EXCLUDED.last_triggered_at
        """,
        (camera_id,),
    )


def should_fire_event(cam_state: dict, event_key: str, cooldown_sec: float) -> bool:
    now = time.time()
    last = float(cam_state.get("last_fire", {}).get(event_key, 0.0))
    return (now - last) >= max(float(cooldown_sec), 0.0)


def mark_event_fired(cam_state: dict, event_key: str):
    cam_state.setdefault("last_fire", {})[event_key] = time.time()


def event_state_for_camera(camera_id: str) -> dict:
    st = CAMERA_EVENT_STATE.get(camera_id)
    if st is None:
        st = {"rules": {}, "last_fire": {}}
        CAMERA_EVENT_STATE[camera_id] = st
    return st


def _in_named_zone(roi: dict, zone_name: str, det: dict) -> bool:
    zone = find_zone(roi, zone_name)
    if not zone:
        return True
    cx, cy = center_of_detection(det)
    return zone_contains(zone, cx, cy)


def _bbox_xyxy(det: dict) -> tuple[float, float, float, float]:
    if all(k in det for k in ("nx", "ny", "nw", "nh")):
        x1 = clamp01(float(det.get("nx", 0.0)))
        y1 = clamp01(float(det.get("ny", 0.0)))
        x2 = clamp01(x1 + float(det.get("nw", 0.0)))
        y2 = clamp01(y1 + float(det.get("nh", 0.0)))
        return x1, y1, x2, y2
    if all(k in det for k in ("x", "y", "w", "h")):
        x1 = clamp01(float(det.get("x", 0.0)))
        y1 = clamp01(float(det.get("y", 0.0)))
        x2 = clamp01(x1 + float(det.get("w", 0.0)))
        y2 = clamp01(y1 + float(det.get("h", 0.0)))
        return x1, y1, x2, y2
    cx, cy = center_of_detection(det)
    return cx, cy, cx, cy


def _merge_inference_payload(payload: dict[str, Any], model_out: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload or {})
    model_payload = model_out.get("payload") if isinstance(model_out.get("payload"), dict) else {}
    raw_dets = model_out.get("detections") if isinstance(model_out.get("detections"), list) else []
    detections = [d for d in raw_dets if isinstance(d, dict)]
    if detections:
        merged["detections"] = detections
    for src_key, dst_key in (
        ("imageWidth", "imageWidth"),
        ("imageHeight", "imageHeight"),
        ("frameWidth", "imageWidth"),
        ("frameHeight", "imageHeight"),
        ("sourceWidth", "imageWidth"),
        ("sourceHeight", "imageHeight"),
    ):
        value = model_payload.get(src_key)
        if value is None:
            continue
        try:
            merged[dst_key] = int(round(float(value)))
        except Exception:
            continue
    return merged


def _person_has_head(person_det: dict, head_dets: list[dict]) -> bool:
    px1, py1, px2, py2 = _bbox_xyxy(person_det)
    for h in head_dets:
        cx, cy = center_of_detection(h)
        if px1 <= cx <= px2 and py1 <= cy <= py2:
            return True
    return False


def _bottom_entry_ratio(roi: dict, zone_name: str, det: dict, samples: int = 11, band_ratio: float = 0.1) -> float:
    zone = find_zone(roi, zone_name)
    if not zone:
        return 1.0
    x1, y1, x2, y2 = _bbox_xyxy(det)
    w = max(x2 - x1, 0.0)
    h = max(y2 - y1, 0.0)
    if w <= 1e-6 or h <= 1e-6:
        return 0.0
    n = max(int(samples), 3)
    band_top = y2 - h * max(min(float(band_ratio), 0.5), 0.01)
    inside = 0
    total = 0
    for yi in (band_top, y2):
        for i in range(n):
            t = i / (n - 1)
            px = x1 + w * t
            py = yi
            total += 1
            if zone_contains(zone, px, py):
                inside += 1
    return (inside / total) if total else 0.0


def _roi_overlap_ratio(roi: dict, zone_name: str, det: dict, grid: int = 5) -> float:
    zone = find_zone(roi, zone_name)
    if not zone:
        return 1.0
    x1, y1, x2, y2 = _bbox_xyxy(det)
    w = max(x2 - x1, 0.0)
    h = max(y2 - y1, 0.0)
    if w <= 1e-6 or h <= 1e-6:
        return 0.0
    n = max(int(grid), 3)
    inside = 0
    total = 0
    for iy in range(n):
        ty = (iy + 0.5) / n
        py = y1 + h * ty
        for ix in range(n):
            tx = (ix + 0.5) / n
            px = x1 + w * tx
            total += 1
            if zone_contains(zone, px, py):
                inside += 1
    return (inside / total) if total else 0.0


def evaluate_event_pack(camera_id: str, roi: dict, model_out: dict, pack_cfg: dict, pack: dict) -> list[dict]:
    if not pack_cfg.get("enabled"):
        return []
    events = pack.get("events") or []
    if not isinstance(events, list):
        return []
    cam_state = event_state_for_camera(camera_id)
    results: list[dict] = []
    overrides = pack_cfg.get("params") if isinstance(pack_cfg.get("params"), dict) else {}
    person_dets = select_detections(model_out, label_is_person)
    head_dets = select_detections(model_out, label_is_head)
    helmet_dets = select_detections(model_out, label_is_helmet)
    vehicle_dets = select_detections(model_out, label_is_vehicle)
    for event_def in events:
        if not isinstance(event_def, dict):
            continue
        key = str(event_def.get("key", "")).strip()
        if not key or not bool(event_def.get("enabled", True)):
            continue
        params = dict(event_def.get("params") or {})
        ov = overrides.get(key)
        if isinstance(ov, dict):
            params.update(ov)
        event_type = str(event_def.get("eventType", key))
        severity = str(event_def.get("severity", "medium"))
        rule_state = cam_state.setdefault("rules", {}).setdefault(key, {})
        now = time.time()

        if key == "person_cross_roi":
            zone_name = str(params.get("roiName", "zone-1"))
            min_conf = float(params.get("minConfidence", 0.35))
            min_entry_ratio = float(params.get("entryRatio", 0.9))
            cooldown = float(params.get("cooldownSec", 10))
            inside = []
            for d in person_dets:
                if float(d.get("confidence", 1.0)) < min_conf:
                    continue
                if _bottom_entry_ratio(roi, zone_name, d) >= min_entry_ratio:
                    inside.append(d)
            prev_inside = int(rule_state.get("prev_inside", 0))
            rule_state["prev_inside"] = len(inside)
            if prev_inside == 0 and len(inside) > 0 and should_fire_event(cam_state, key, cooldown):
                mark_event_fired(cam_state, key)
                results.append(
                    {
                        "eventType": event_type,
                        "severity": severity,
                        "payload": {"rule": key, "count": len(inside), "roi": zone_name, "entryRatio": min_entry_ratio},
                    }
                )

        elif key == "helmet_missing_in_roi":
            zone_name = str(params.get("roiName", "zone-1"))
            min_p = float(params.get("minConfidencePerson", 0.35))
            min_head = float(params.get("minConfidenceHead", min_p))
            min_h = float(params.get("minConfidenceHelmet", 0.35))
            hold_sec = float(params.get("holdSec", 3))
            cooldown = float(params.get("cooldownSec", 20))
            # Helmet-missing rule is intentionally global (full frame), not ROI-limited.
            person_inside = [d for d in person_dets if float(d.get("confidence", 1.0)) >= min_p]
            head_inside = [d for d in head_dets if float(d.get("confidence", 1.0)) >= min_head]
            helmet_inside = [d for d in helmet_dets if float(d.get("confidence", 1.0)) >= min_h]
            person_with_head = [p for p in person_inside if _person_has_head(p, head_inside)]
            # Fallback: if head is not detected at all, treat "person present & helmet absent" as helmet-missing.
            missing_by_head = bool(person_with_head and not helmet_inside)
            missing_by_person_only = bool((not head_inside) and person_inside and (not helmet_inside))
            missing_now = bool(missing_by_head or missing_by_person_only)
            if missing_now:
                if not rule_state.get("missing_since"):
                    rule_state["missing_since"] = now
                missing_for = now - float(rule_state.get("missing_since", now))
                is_active = bool(rule_state.get("missing_active", False))
                if (not is_active) and missing_for >= hold_sec and should_fire_event(cam_state, key, cooldown):
                    mark_event_fired(cam_state, key)
                    rule_state["missing_active"] = True
                    results.append(
                        {
                            "eventType": event_type,
                            "severity": severity,
                            "payload": {
                                "rule": key,
                                "roi": zone_name,
                                "personCount": len(person_inside),
                                "headCount": len(head_inside),
                                "personWithHeadCount": len(person_with_head),
                                "helmetCount": 0,
                                "fallbackNoHead": bool(missing_by_person_only),
                                "missingForSec": round(missing_for, 2),
                            },
                        }
                    )
            else:
                rule_state["missing_since"] = None
                rule_state["missing_active"] = False

        elif key == "vehicle_move_without_signalman":
            vehicle_roi = str(params.get("vehicleRoiName", "zone-1"))
            person_roi = str(params.get("personRoiName", vehicle_roi))
            min_v = float(params.get("minConfidenceVehicle", 0.45))
            min_seen = float(params.get("minVehicleSeenSec", 2.0))
            exit_hold = float(params.get("exitHoldSec", 1.5))
            cooldown = float(params.get("cooldownSec", 30))
            vehicles = [d for d in vehicle_dets if float(d.get("confidence", 1.0)) >= min_v and _in_named_zone(roi, vehicle_roi, d)]
            persons = [d for d in person_dets if _in_named_zone(roi, person_roi, d)]
            has_vehicle = len(vehicles) > 0
            has_person = len(persons) > 0
            prev_vehicle_inside = bool(rule_state.get("prev_vehicle_inside", False))
            if has_vehicle:
                if not rule_state.get("vehicle_seen_since"):
                    rule_state["vehicle_seen_since"] = now
                seen_for = now - float(rule_state.get("vehicle_seen_since", now))
                if seen_for >= min_seen:
                    rule_state["vehicle_qualified"] = True
            else:
                rule_state["vehicle_seen_since"] = None
            vehicle_qualified = bool(rule_state.get("vehicle_qualified", False))
            if prev_vehicle_inside and (not has_vehicle) and (not has_person) and vehicle_qualified:
                if not rule_state.get("exit_since"):
                    rule_state["exit_since"] = now
                exited_for = now - float(rule_state.get("exit_since", now))
                if exited_for >= exit_hold and should_fire_event(cam_state, key, cooldown):
                    mark_event_fired(cam_state, key)
                    results.append(
                        {
                            "eventType": event_type,
                            "severity": severity,
                            "payload": {
                                "rule": key,
                                "vehicleCount": len(vehicles),
                                "personCount": len(persons),
                                "vehicleRoiName": vehicle_roi,
                                "personRoiName": person_roi,
                                "minConfidenceVehicle": min_v,
                                "minVehicleSeenSec": min_seen,
                                "exitedForSec": round(exited_for, 2),
                            },
                        }
                    )
            else:
                rule_state["exit_since"] = None
            if not has_vehicle:
                rule_state["vehicle_qualified"] = False
            rule_state["prev_vehicle_inside"] = has_vehicle

        elif key == "no_parking_stop":
            zone_name = str(params.get("roiName", "zone-1"))
            min_v = float(params.get("minConfidenceVehicle", 0.35))
            min_overlap = float(params.get("minRoiOverlap", 0.7))
            stop_thr = float(params.get("stopMotionThreshold", 0.01))
            dwell_sec = float(params.get("dwellSec", 300))
            miss_grace = float(params.get("missGraceSec", 5))
            track_max_dist = float(params.get("trackMaxCenterDist", 0.12))
            cooldown = float(params.get("cooldownSec", 30))
            candidates = []
            for d in vehicle_dets:
                if float(d.get("confidence", 1.0)) < min_v:
                    continue
                overlap = _roi_overlap_ratio(roi, zone_name, d)
                if overlap < min_overlap:
                    continue
                cx, cy = center_of_detection(d)
                candidates.append({"det": d, "cx": cx, "cy": cy, "overlap": overlap, "conf": float(d.get("confidence", 0.0))})

            prev_track = rule_state.get("tracked_center")
            selected = None
            selected_dist = None
            if candidates:
                if isinstance(prev_track, list) and len(prev_track) == 2:
                    pcx, pcy = float(prev_track[0]), float(prev_track[1])
                    ranked = sorted(candidates, key=lambda c: ((c["cx"] - pcx) ** 2 + (c["cy"] - pcy) ** 2) ** 0.5)
                    selected = ranked[0]
                    selected_dist = ((selected["cx"] - pcx) ** 2 + (selected["cy"] - pcy) ** 2) ** 0.5
                    if selected_dist > track_max_dist:
                        # Likely a different passing vehicle: reset stationary timer.
                        selected = max(candidates, key=lambda c: (c["overlap"], c["conf"]))
                        rule_state["stationary_since"] = None
                        selected_dist = None
                else:
                    selected = max(candidates, key=lambda c: (c["overlap"], c["conf"]))

            moving = False
            has_effective_target = False
            if selected is not None:
                cx, cy = float(selected["cx"]), float(selected["cy"])
                prev_center = rule_state.get("prev_center")
                if isinstance(prev_center, list) and len(prev_center) == 2:
                    dx = cx - float(prev_center[0])
                    dy = cy - float(prev_center[1])
                    moving = (dx * dx + dy * dy) ** 0.5 >= stop_thr
                else:
                    moving = False
                rule_state["prev_center"] = [cx, cy]
                rule_state["tracked_center"] = [cx, cy]
                rule_state["last_seen_at"] = now
                has_effective_target = True
            else:
                last_seen_at = float(rule_state.get("last_seen_at", 0.0) or 0.0)
                if last_seen_at > 0 and (now - last_seen_at) <= miss_grace:
                    # Keep stationary timer during short occlusion/missed detection.
                    has_effective_target = True
                    moving = False
                else:
                    rule_state["prev_center"] = None
                    rule_state["tracked_center"] = None
                    rule_state["last_seen_at"] = 0.0
                    rule_state["stationary_since"] = None
                    continue

            if has_effective_target and not moving:
                if not rule_state.get("stationary_since"):
                    rule_state["stationary_since"] = now
                parked_for = now - float(rule_state.get("stationary_since", now))
                if parked_for >= dwell_sec and should_fire_event(cam_state, key, cooldown):
                    mark_event_fired(cam_state, key)
                    results.append(
                        {
                            "eventType": event_type,
                            "severity": severity,
                            "payload": {
                                "rule": key,
                                "roi": zone_name,
                                "vehicleCount": len(candidates),
                                "dwellSec": round(parked_for, 2),
                                "missGraceSec": miss_grace,
                                "minRoiOverlap": min_overlap,
                                "trackMaxCenterDist": track_max_dist,
                                "trackDistance": None if selected_dist is None else round(float(selected_dist), 4),
                            },
                        }
                    )
            else:
                rule_state["stationary_since"] = None
    return results


def ring_runtime_info(camera_id: str) -> dict:
    return RING_BUFFER_MANAGER.runtime_info(camera_id)


def upsert_camera_health(cur, camera_id: str, connected: bool, connect_reason: str):
    ring = ring_runtime_info(camera_id)
    cur.execute(
        """
        INSERT INTO recorder_camera_health (
          camera_id, connected, last_connect_reason, ring_running, ring_restart_count, last_ring_exit_code, last_probe_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (camera_id) DO UPDATE SET
          connected = EXCLUDED.connected,
          last_connect_reason = EXCLUDED.last_connect_reason,
          ring_running = EXCLUDED.ring_running,
          ring_restart_count = EXCLUDED.ring_restart_count,
          last_ring_exit_code = EXCLUDED.last_ring_exit_code,
          last_probe_at = NOW(),
          updated_at = NOW()
        """,
        (
            camera_id,
            connected,
            connect_reason,
            ring["running"],
            ring["restart_count"],
            ring["last_exit_code"],
        ),
    )


def detect_events_once() -> int:
    created = 0
    with db_conn() as conn, conn.cursor() as cur:
        global_cfg = get_ai_model_settings(cur)
        person_event_rule = get_person_event_rule(cur)

        cur.execute("SELECT * FROM cameras WHERE enabled = TRUE ORDER BY created_at ASC LIMIT 8")
        cameras = cur.fetchall()
        for cam in cameras:
            cam_id = str(cam["id"])
            connected, connect_reason = ensure_camera_connected(cur, cam)
            if not connected:
                stop_ring_recorder(cam_id)
                upsert_camera_health(cur, cam_id, False, connect_reason)
                cur.execute(
                    """
                    INSERT INTO ai_detection_logs (camera_id, trigger, score, label, detail_json)
                    VALUES (%s, FALSE, NULL, NULL, %s)
                    """,
                    (
                        cam["id"],
                        json.dumps({"reason": "camera_not_connected", "detail": connect_reason}),
                    ),
                )
                continue

            cam_cfg = get_camera_model_settings(cur, cam_id, global_cfg)
            if not cam_cfg.get("enabled", False):
                upsert_camera_health(cur, cam_id, True, "connected:model_disabled")
                continue

            ensure_ring_recorder(cam)
            upsert_camera_health(cur, cam_id, True, connect_reason)
            roi = get_camera_roi(cur, cam_id)
            trigger, detail = run_ai_model_for_camera(cam_cfg, cam, roi, person_event_rule)
            score = None
            label = None
            out = detail.get("modelOutput", {})
            if isinstance(out, dict):
                score = out.get("score")
                label = out.get("label")

            cur.execute(
                """
                INSERT INTO ai_detection_logs (camera_id, trigger, score, label, detail_json)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (cam["id"], trigger, score, label, json.dumps(detail or {})),
            )

            # Event-pack rules from detector outputs (detections list).
            event_pack_enabled = False
            event_pack_created = 0
            if isinstance(out, dict):
                pack_cfg = get_camera_event_pack_settings(cur, cam_id)
                if pack_cfg.get("enabled"):
                    pack = get_event_pack(str(pack_cfg.get("packId", "")), str(pack_cfg.get("packVersion", "")))
                    if pack:
                        event_pack_enabled = True
                        for ev in evaluate_event_pack(cam_id, roi, out, pack_cfg, pack):
                            ev_type = str(ev.get("eventType", "motion"))
                            if not event_policy_allows(cur, cam_id, ev_type):
                                continue
                            payload = ev.get("payload", {}) if isinstance(ev.get("payload"), dict) else {}
                            payload = _merge_inference_payload(payload, out)
                            payload["source"] = "event_pack"
                            payload["packId"] = pack_cfg.get("packId")
                            payload["packVersion"] = pack_cfg.get("packVersion")
                            payload["modelPath"] = cam_cfg.get("modelPath")
                            cur.execute(
                                """
                                INSERT INTO events (camera_id, event_type, severity, occurred_at, payload_json)
                                VALUES (%s, %s, %s, NOW(), %s)
                                RETURNING id, camera_id, event_type, severity, occurred_at, payload_json
                                """,
                                (
                                    cam["id"],
                                    ev_type,
                                    str(ev.get("severity", "medium")),
                                    json.dumps(payload or {}),
                                ),
                            )
                            event_row = cur.fetchone()
                            append_event_log(
                                event_id=event_row["id"],
                                camera_id=event_row["camera_id"],
                                event_type=event_row["event_type"],
                                severity=event_row["severity"],
                                occurred_at=event_row["occurred_at"],
                                payload=event_row["payload_json"],
                                source="recorder_event_pack",
                            )
                            created += 1
                            event_pack_created += 1

                model_events = out.get("events") if isinstance(out.get("events"), list) else []
                for mev in model_events:
                    if not isinstance(mev, dict):
                        continue
                    ev_type = str(mev.get("eventType", "motion"))
                    if not event_policy_allows(cur, cam_id, ev_type):
                        continue
                    payload = mev.get("payload", {}) if isinstance(mev.get("payload"), dict) else {}
                    payload = _merge_inference_payload(payload, out)
                    payload["source"] = "ai_model_event"
                    payload["modelPath"] = cam_cfg.get("modelPath")
                    cur.execute(
                        """
                        INSERT INTO events (camera_id, event_type, severity, occurred_at, payload_json)
                        VALUES (%s, %s, %s, NOW(), %s)
                        RETURNING id, camera_id, event_type, severity, occurred_at, payload_json
                        """,
                        (
                            cam["id"],
                            ev_type,
                            str(mev.get("severity", "medium")),
                            json.dumps(payload or {}),
                        ),
                    )
                    event_row = cur.fetchone()
                    append_event_log(
                        event_id=event_row["id"],
                        camera_id=event_row["camera_id"],
                        event_type=event_row["event_type"],
                        severity=event_row["severity"],
                        occurred_at=event_row["occurred_at"],
                        payload=event_row["payload_json"],
                        source="recorder_model_event",
                    )
                    created += 1

            # Backward-compatible single trigger mode.
            # If event-pack is enabled but emitted no event (e.g., strict pack thresholds),
            # keep single-trigger fallback so runtime trigger is not dropped.
            allow_single_fallback = (not event_pack_enabled) or (event_pack_created == 0)
            if trigger and allow_single_fallback:
                event_type = out.get("eventType", "motion") if isinstance(out, dict) else "motion"
                severity = out.get("severity", "medium") if isinstance(out, dict) else "medium"
                payload = out.get("payload", {}) if isinstance(out, dict) else {}
                if not isinstance(payload, dict):
                    payload = {"rawPayload": payload}
                payload = _merge_inference_payload(payload, out if isinstance(out, dict) else {})
                if not event_policy_allows(cur, cam_id, str(event_type)):
                    continue
                if not should_trigger(cur, cam_id, int(cam_cfg.get("cooldownSec", 10))):
                    continue
                payload["source"] = "ai_model"
                payload["modelPath"] = cam_cfg.get("modelPath")
                payload["score"] = score
                payload["label"] = label
                cur.execute(
                    """
                    INSERT INTO events (camera_id, event_type, severity, occurred_at, payload_json)
                    VALUES (%s, %s, %s, NOW(), %s)
                    RETURNING id, camera_id, event_type, severity, occurred_at, payload_json
                    """,
                    (cam["id"], event_type, severity, json.dumps(payload or {})),
                )
                event_row = cur.fetchone()
                append_event_log(
                    event_id=event_row["id"],
                    camera_id=event_row["camera_id"],
                    event_type=event_row["event_type"],
                    severity=event_row["severity"],
                    occurred_at=event_row["occurred_at"],
                    payload=event_row["payload_json"],
                    source="recorder_model_fallback",
                )
                mark_triggered(cur, cam_id)
                created += 1
        conn.commit()
    return created


def build_artifacts_once() -> int:
    processed = 0
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.*
            FROM events e
            LEFT JOIN artifacts a ON a.event_id = e.id
            WHERE a.id IS NULL
            ORDER BY e.occurred_at ASC
            LIMIT 20
            """
        )
        events = cur.fetchall()
        for ev in events:
            cur.execute(
                """
                SELECT *
                FROM event_policies
                WHERE camera_id = %s AND event_type = %s
                """,
                (ev["camera_id"], ev["event_type"]),
            )
            policy = cur.fetchone()
            kind = "snapshot"
            clip_pre_sec = 10
            clip_duration_sec = 10
            if policy and policy["mode"] == "clip":
                kind = "clip"
                clip_pre_sec = int(policy["clip_pre_sec"])
                clip_duration_sec = int(policy["clip_post_sec"])

            cur.execute(
                """
                SELECT c.rtsp_url, c.name AS camera_name, cms.extra_json
                FROM cameras c
                LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
                WHERE c.id = %s
                """,
                (ev["camera_id"],),
            )
            cam = cur.fetchone()
            rtsp_url = cam["rtsp_url"] if cam else ""
            camera_name = str((cam or {}).get("camera_name") or "")
            extra_json = cam["extra_json"] if cam else {}
            rotate_deg = normalize_rotate_deg((extra_json or {}).get("rotationDeg", 0) if isinstance(extra_json, dict) else 0)
            transfer_stem = build_artifact_stem(DEVICE_NAME, camera_name, str(ev["event_type"]), ev["occurred_at"])

            if kind == "clip" and USE_FFMPEG_ARTIFACTS and ENABLE_RTSP_RING_BUFFER:
                # Wait until post-event window has passed, then assemble pre/post from ring segments.
                occurred = ev["occurred_at"]
                now_dt = datetime.now(timezone.utc)
                ready_at = occurred + timedelta(seconds=max(clip_duration_sec, 1))
                if now_dt < ready_at:
                    continue
                start_ts = occurred - timedelta(seconds=max(clip_pre_sec, 0))
                end_ts = occurred + timedelta(seconds=max(clip_duration_sec, 1))
                ring_clip = build_clip_from_ring(str(ev["camera_id"]), str(ev["id"]), start_ts, end_ts)
                if ring_clip:
                    path = ring_clip
                    mime = "video/mp4"
                else:
                    path, mime = make_artifact_file(
                        kind,
                        str(ev["camera_id"]),
                        str(ev["id"]),
                        rtsp_url,
                        clip_duration_sec,
                        rotate_deg=rotate_deg,
                        artifact_stem=transfer_stem,
                    )
            else:
                path, mime = make_artifact_file(
                    kind,
                    str(ev["camera_id"]),
                    str(ev["id"]),
                    rtsp_url,
                    clip_duration_sec,
                    rotate_deg=rotate_deg,
                    artifact_stem=transfer_stem,
                )
            checksum = sha256_file(path)
            size_bytes = path.stat().st_size

            cur.execute(
                """
                INSERT INTO artifacts (
                  event_id, camera_id, kind, local_path, uri, mime_type, checksum_sha256, size_bytes
                )
                VALUES (%s, %s, %s, %s, NULL, %s, %s, %s)
                RETURNING id
                """,
                (ev["id"], ev["camera_id"], kind, str(path), mime, checksum, size_bytes),
            )
            artifact = cur.fetchone()

            if DELIVERY_DEDUP_WINDOW_SEC > 0:
                cur.execute(
                    """
                    INSERT INTO delivery_attempts (artifact_id, destination_id, status, attempt_no, next_retry_at)
                    SELECT %s, rr.destination_id, 'queued', 1, NOW()
                    FROM routing_rules rr
                    WHERE rr.camera_id = %s
                      AND (rr.event_type = %s OR rr.event_type = '*' OR rr.event_type = 'all')
                      AND rr.enabled = TRUE
                      AND (rr.artifact_kind = %s OR rr.artifact_kind = 'both')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM delivery_attempts da2
                        JOIN artifacts a2 ON a2.id = da2.artifact_id
                        JOIN events e2 ON e2.id = a2.event_id
                        WHERE da2.destination_id = rr.destination_id
                          AND da2.status IN ('queued', 'in_progress', 'success')
                          AND a2.camera_id = %s
                          AND a2.kind = %s
                          AND e2.event_type = %s
                          AND e2.occurred_at >= (%s::timestamptz - (%s * INTERVAL '1 second'))
                      )
                    """,
                    (
                        artifact["id"],
                        ev["camera_id"],
                        ev["event_type"],
                        kind,
                        ev["camera_id"],
                        kind,
                        ev["event_type"],
                        ev["occurred_at"],
                        DELIVERY_DEDUP_WINDOW_SEC,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO delivery_attempts (artifact_id, destination_id, status, attempt_no, next_retry_at)
                    SELECT %s, rr.destination_id, 'queued', 1, NOW()
                    FROM routing_rules rr
                    WHERE rr.camera_id = %s
                      AND (rr.event_type = %s OR rr.event_type = '*' OR rr.event_type = 'all')
                      AND rr.enabled = TRUE
                      AND (rr.artifact_kind = %s OR rr.artifact_kind = 'both')
                    """,
                    (artifact["id"], ev["camera_id"], ev["event_type"], kind),
                )
            processed += 1
        conn.commit()
    return processed


def main():
    ensure_dirs()
    try:
        while True:
            prune_oldest_logs_if_low_disk()
            created = detect_events_once()
            processed = build_artifacts_once()
            with db_conn() as conn, conn.cursor() as cur:
                cfg = get_ai_model_settings(cur)
            if created == 0 and processed == 0:
                time.sleep(cfg["pollSec"])
    finally:
        for cam_id in list(RING_BUFFER_MANAGER.procs.keys()):
            stop_ring_recorder(cam_id)


if __name__ == "__main__":
    main()

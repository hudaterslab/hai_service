import os
import json
import socket
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("DEV_DB_PATH", str(ROOT / "data" / "dev.db")))
POLL_SEC = float(os.getenv("DEV_RECORDER_POLL_SEC", "2.0"))
CONNECT_TIMEOUT_SEC = float(os.getenv("DEV_RECORDER_CONNECT_TIMEOUT_SEC", "2.5"))
MAX_BACKOFF_SEC = int(os.getenv("DEV_RECORDER_MAX_BACKOFF_SEC", "60"))
MODEL_PYTHON_BIN = os.getenv("MODEL_PYTHON_BIN", "python")
RTSP_FALLBACK_PATH = os.getenv("DEV_AI_RTSP_FALLBACK_PATH", "/Streaming/Channels/101")

RUNTIME: dict[str, dict] = {}
LAST_TRIGGER_TS: dict[str, float] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


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
            req = f"OPTIONS {rtsp_url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: vms-dev-recorder\r\n\r\n"
            s.sendall(req.encode("utf-8"))
            resp = s.recv(512).decode("utf-8", errors="ignore")
            if "RTSP/1.0" in resp:
                return True, "ok"
            return True, "tcp_connected_no_rtsp_header"
    except Exception as ex:
        return False, str(ex)


def backoff_seconds(fail_count: int) -> int:
    n = max(fail_count - 1, 0)
    return min(2**n, MAX_BACKOFF_SEC)


def get_state(camera_id: str) -> dict:
    st = RUNTIME.get(camera_id)
    if not st:
        st = {"connected": False, "fail_count": 0, "next_retry_ts": 0.0}
        RUNTIME[camera_id] = st
    return st


def update_health(c: sqlite3.Connection, camera_id: str, connected: bool, reason: str):
    c.execute(
        """
        INSERT INTO recorder_camera_health (
          camera_id, connected, last_connect_reason, ring_running, ring_restart_count, last_ring_exit_code, last_probe_at, updated_at
        )
        VALUES (?, ?, ?, 0, 0, NULL, ?, ?)
        ON CONFLICT(camera_id) DO UPDATE SET
          connected=excluded.connected,
          last_connect_reason=excluded.last_connect_reason,
          last_probe_at=excluded.last_probe_at,
          updated_at=excluded.updated_at
        """,
        (camera_id, 1 if connected else 0, reason, now_iso(), now_iso()),
    )


def set_status(c: sqlite3.Connection, camera_id: str, status: str):
    c.execute("UPDATE cameras SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), camera_id))


def normalize_rtsp_url(rtsp_url: str) -> str:
    if not rtsp_url:
        return rtsp_url
    if rtsp_url.startswith("rtsp://") and rtsp_url.count("/") <= 2:
        return rtsp_url.rstrip("/") + RTSP_FALLBACK_PATH
    return rtsp_url


def get_ai_model_settings(c: sqlite3.Connection) -> dict:
    row = c.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'").fetchone()
    if not row:
        return {"enabled": False, "modelPath": "", "timeoutSec": 5, "pollSec": 2, "cooldownSec": 10}
    raw = {}
    try:
        raw = json.loads(row["value_json"] or "{}")
    except Exception:
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "modelPath": str(raw.get("modelPath", "")),
        "timeoutSec": max(int(raw.get("timeoutSec", 5)), 1),
        "pollSec": max(float(raw.get("pollSec", 2)), 0.0),
        "cooldownSec": max(int(raw.get("cooldownSec", 10)), 0),
    }


def get_person_event_rule(c: sqlite3.Connection) -> dict:
    row = c.execute("SELECT value_json FROM app_settings WHERE key = 'person_event_rule'").fetchone()
    if not row:
        return {"enabled": True, "dwellSec": 5, "cooldownSec": 10, "eventType": "person_detected", "severity": "high"}
    raw = {}
    try:
        raw = json.loads(row["value_json"] or "{}")
    except Exception:
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "dwellSec": max(int(raw.get("dwellSec", 5)), 1),
        "cooldownSec": max(int(raw.get("cooldownSec", 10)), 0),
        "eventType": str(raw.get("eventType", "person_detected") or "person_detected"),
        "severity": str(raw.get("severity", "high") or "high"),
    }


def get_camera_roi(c: sqlite3.Connection, camera_id: str) -> dict:
    row = c.execute("SELECT enabled, zones_json FROM camera_rois WHERE camera_id = ?", (camera_id,)).fetchone()
    if not row:
        return {"enabled": False, "zones": []}
    zones = []
    try:
        zones = json.loads(row["zones_json"] or "[]")
    except Exception:
        zones = []
    return {"enabled": bool(row["enabled"]), "zones": zones}


def run_ai_model_for_camera(cfg: dict, cam: sqlite3.Row, roi: dict) -> tuple[bool, dict]:
    model_path = cfg["modelPath"].strip()
    if not model_path:
        return False, {"reason": "ai_model_path_empty"}
    if not Path(model_path).exists():
        return False, {"reason": "ai_model_path_not_found", "modelPath": model_path}

    runner_candidates = [
        os.getenv("DEFAULT_AI_MODEL_RUNNER", "").strip(),
        str(ROOT / "models" / "yolo_person_exit_model.py"),
        "/opt/vms/models/yolo_person_exit_model.py",
    ]
    runner_path = next((Path(p) for p in runner_candidates if p and Path(p).exists()), None)
    run_env = os.environ.copy()
    if model_path.lower().endswith(".py"):
        cmd = [MODEL_PYTHON_BIN, model_path]
    else:
        if runner_path is None:
            return False, {
                "reason": "ai_model_runner_not_found",
                "runnerPath": "",
                "modelPath": model_path,
            }
        cmd = [MODEL_PYTHON_BIN, str(runner_path)]
        run_env["YOLO_MODEL_PATH"] = model_path
        run_env.setdefault("YOLO_CONFIG_DIR", str(ROOT / "data" / ".ultralytics"))
        Path(run_env["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
    req = {
        "cameraId": str(cam["id"]),
        "cameraName": cam["name"],
        "eventType": "motion",
        "rtspUrl": normalize_rtsp_url(str(cam["rtsp_url"])),
        "webrtcPath": cam["webrtc_path"],
        "timestamp": now_iso(),
        "roi": roi,
    }
    if cfg.get("personEventRule"):
        req["personEventRule"] = cfg["personEventRule"]
    try:
        p = subprocess.run(
            cmd,
            input=json.dumps(req),
            capture_output=True,
            text=True,
            timeout=cfg["timeoutSec"],
            check=False,
            env=run_env,
        )
    except Exception as ex:
        return False, {"reason": "execute_failed", "error": str(ex)}

    stdout = (p.stdout or "").strip()
    stderr = (p.stderr or "").strip()
    detail = {"exitCode": p.returncode, "stdout": stdout[:500], "stderr": stderr[:500]}
    if p.returncode != 0:
        return False, {**detail, "reason": "non_zero_exit"}
    if not stdout:
        return False, {**detail, "reason": "empty_stdout"}
    try:
        out = json.loads(stdout)
        trigger = bool(out.get("trigger", False))
        detail["modelOutput"] = out
        return trigger, detail
    except Exception:
        s = stdout.lower()
        trigger = s in ("1", "true", "yes", "trigger", "pass")
        detail["modelOutputRaw"] = stdout[:200]
        return trigger, detail


def should_trigger(camera_id: str, cooldown_sec: int) -> bool:
    if cooldown_sec <= 0:
        return True
    last = LAST_TRIGGER_TS.get(camera_id)
    if not last:
        return True
    return (time.time() - last) >= cooldown_sec


def mark_triggered(camera_id: str):
    LAST_TRIGGER_TS[camera_id] = time.time()


def create_event(c: sqlite3.Connection, cam: sqlite3.Row, detail: dict):
    model_out = detail.get("modelOutput", {}) if isinstance(detail, dict) else {}
    event_type = str(model_out.get("eventType", "motion"))
    severity = str(model_out.get("severity", "medium"))
    payload = {
        "source": "ai_model",
        "label": model_out.get("label"),
        "score": model_out.get("score"),
        "modelOutput": model_out,
        "detail": detail,
    }
    c.execute(
        """
        INSERT INTO events (id, camera_id, event_type, severity, occurred_at, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid4()), str(cam["id"]), event_type, severity, now_iso(), json.dumps(payload), now_iso()),
    )


def tick() -> int:
    changed = 0
    with conn() as c:
        ai_cfg = get_ai_model_settings(c)
        rows = c.execute("SELECT id, rtsp_url FROM cameras WHERE enabled = 1 ORDER BY created_at ASC LIMIT 8").fetchall()
        for r in rows:
            cam_id = str(r["id"])
            st = get_state(cam_id)
            now = time.time()
            if not st["connected"] and now < st["next_retry_ts"]:
                wait_sec = int(st["next_retry_ts"] - now)
                update_health(c, cam_id, False, f"reconnect_backoff_wait:{wait_sec}s")
                continue

            ok, reason = probe_rtsp(str(r["rtsp_url"]), CONNECT_TIMEOUT_SEC)
            if ok:
                if not st["connected"]:
                    changed += 1
                st["connected"] = True
                st["fail_count"] = 0
                st["next_retry_ts"] = 0.0
                set_status(c, cam_id, "online")
                update_health(c, cam_id, True, "connected")
            else:
                if st["connected"]:
                    changed += 1
                st["connected"] = False
                st["fail_count"] += 1
                delay = backoff_seconds(st["fail_count"])
                st["next_retry_ts"] = now + delay
                set_status(c, cam_id, "offline")
                update_health(c, cam_id, False, f"connect_failed:{reason}")

        person_rule = get_person_event_rule(c)
        if ai_cfg["enabled"]:
            cams = c.execute(
                "SELECT id, name, rtsp_url, webrtc_path FROM cameras WHERE enabled = 1 AND status = 'online' ORDER BY created_at ASC LIMIT 8"
            ).fetchall()
            for cam in cams:
                camera_id = str(cam["id"])
                roi = get_camera_roi(c, camera_id)
                local_ai_cfg = dict(ai_cfg)
                local_ai_cfg["personEventRule"] = person_rule
                trigger, detail = run_ai_model_for_camera(local_ai_cfg, cam, roi)
                if not trigger:
                    continue
                cooldown_sec = ai_cfg["cooldownSec"]
                if person_rule.get("enabled", True):
                    cooldown_sec = int(person_rule.get("cooldownSec", cooldown_sec))
                if not should_trigger(camera_id, cooldown_sec):
                    continue
                create_event(c, cam, detail)
                mark_triggered(camera_id)
        c.commit()
    return changed


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"dev recorder running. db={DB_PATH} poll={POLL_SEC}s timeout={CONNECT_TIMEOUT_SEC}s")
    while True:
        try:
            tick()
        except Exception as ex:
            print(f"[dev-recorder] tick error: {ex}")
        sleep_sec = max(POLL_SEC, 0.5)
        try:
            with conn() as c:
                ai_cfg = get_ai_model_settings(c)
            if ai_cfg["enabled"]:
                # Allow near real-time mode when pollSec is set to 0.
                sleep_sec = max(float(ai_cfg.get("pollSec", 2.0)), 0.1)
        except Exception:
            pass
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()

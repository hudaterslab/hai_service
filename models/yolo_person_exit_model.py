import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


ABSENCE_SEC = float(os.getenv("PERSON_EXIT_ABSENCE_SEC", "3.0"))
CONF_THRES = float(os.getenv("PERSON_EXIT_CONF_THRES", "0.25"))
STATE_DIR = Path(os.getenv("PERSON_EXIT_STATE_DIR", str(Path(__file__).parent / ".runtime" / "person_exit_state")))
DEFAULT_MODEL_PATH = str(Path(__file__).with_name("yolov8n.pt"))
MODEL_PATH = os.getenv("YOLO_MODEL_PATH", DEFAULT_MODEL_PATH)
YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")
FRAME_READ_TIMEOUT_SEC = float(os.getenv("PERSON_EXIT_FRAME_TIMEOUT_SEC", "4.0"))
MAX_FRAME_READ_TRIES = int(os.getenv("PERSON_EXIT_FRAME_MAX_TRIES", "20"))
PERSON_CLASS_ID = int(os.getenv("PERSON_CLASS_ID", "0"))
YOLO_CONFIG_DIR = Path(
    os.getenv("YOLO_CONFIG_DIR", str(Path(__file__).parent / ".runtime" / "ultralytics_config"))
)
os.environ.setdefault("YOLO_CONFIG_DIR", str(YOLO_CONFIG_DIR))
MODEL_CACHE: dict[str, Any] = {}


def safe_camera_key(camera_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", camera_id or "unknown")


def load_state(camera_id: str) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{safe_camera_key(camera_id)}.json"
    if not path.exists():
        return {
            "last_seen_ts": None,
            "absent_start_ts": None,
            "exit_fired": False,
            "present_start_ts": None,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_seen_ts": None,
            "absent_start_ts": None,
            "exit_fired": False,
            "present_start_ts": None,
        }


def save_state(camera_id: str, st: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{safe_camera_key(camera_id)}.json"
    path.write_text(json.dumps(st, ensure_ascii=True), encoding="utf-8")


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def point_in_polygon(px: float, py: float, points: list[dict[str, Any]]) -> bool:
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


def point_in_zone(cx: float, cy: float, z: dict[str, Any]) -> bool:
    shape = str(z.get("shape", "rect")).lower()
    if shape == "polygon":
        points = z.get("points") or []
        if not isinstance(points, list):
            return False
        clean_points: list[dict[str, float]] = []
        for p in points:
            if not isinstance(p, dict):
                continue
            clean_points.append({"x": clamp01(float(p.get("x", 0.0))), "y": clamp01(float(p.get("y", 0.0)))})
        return point_in_polygon(clamp01(cx), clamp01(cy), clean_points)
    x = clamp01(float(z.get("x", 0.0)))
    y = clamp01(float(z.get("y", 0.0)))
    w = clamp01(float(z.get("w", 0.0)))
    h = clamp01(float(z.get("h", 0.0)))
    return (x <= cx <= x + w) and (y <= cy <= y + h)


def inside_enabled_roi(cx: float, cy: float, req: dict[str, Any]) -> bool:
    roi = req.get("roi") or {}
    if not bool(roi.get("enabled", False)):
        return True
    zones = roi.get("zones") or []
    if not zones:
        return True
    valid_zone_count = 0
    for z in zones:
        if not isinstance(z, dict):
            continue
        valid_zone_count += 1
        if point_in_zone(cx, cy, z):
            return True
    return valid_zone_count == 0


def apply_rotation(frame, rotate_deg: int):
    import cv2  # type: ignore

    if rotate_deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rotate_deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotate_deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def _resolve_model_path(req: dict[str, Any]) -> str:
    req_model = str(req.get("modelPath", "") or "").strip()
    return req_model or MODEL_PATH


def get_frame(source_path: str, offset_sec: float = 0.0):
    import cv2  # type: ignore

    cap = cv2.VideoCapture(source_path, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("video_capture_open_failed")
    try:
        if offset_sec > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(offset_sec, 0.0) * 1000.0)
        start = time.time()
        for _ in range(max(MAX_FRAME_READ_TRIES, 1)):
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame
            if (time.time() - start) >= max(FRAME_READ_TIMEOUT_SEC, 0.5):
                break
            time.sleep(0.05)
    finally:
        cap.release()
    raise RuntimeError("video_capture_read_timeout")


def detect_person(frame, req: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    from ultralytics import YOLO  # type: ignore

    try:
        conf_thres = float(req.get("confidenceThreshold", CONF_THRES))
    except Exception:
        conf_thres = CONF_THRES
    conf_thres = max(0.05, min(conf_thres, 0.95))

    model_path = _resolve_model_path(req)
    model = MODEL_CACHE.get(model_path)
    if model is None:
        model = YOLO(model_path)
        MODEL_CACHE[model_path] = model
    result = model.predict(
        source=frame,
        conf=conf_thres,
        verbose=False,
        device=YOLO_DEVICE,
    )[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return 0, []
    cls = boxes.cls.tolist() if getattr(boxes, "cls", None) is not None else []
    confs = boxes.conf.tolist() if getattr(boxes, "conf", None) is not None else []
    xyxy = boxes.xyxy.tolist() if getattr(boxes, "xyxy", None) is not None else []
    count = 0
    detections: list[dict[str, Any]] = []
    h, w = frame.shape[:2]
    for i, c in enumerate(cls):
        if int(c) != PERSON_CLASS_ID:
            continue
        if i >= len(xyxy):
            continue
        x1, y1, x2, y2 = xyxy[i]
        cx = max(0.0, min(1.0, ((x1 + x2) * 0.5) / max(float(w), 1.0)))
        cy = max(0.0, min(1.0, ((y1 + y2) * 0.5) / max(float(h), 1.0)))
        if inside_enabled_roi(cx, cy, req):
            count += 1
            nx1 = max(0.0, min(1.0, float(x1) / max(float(w), 1.0)))
            ny1 = max(0.0, min(1.0, float(y1) / max(float(h), 1.0)))
            nx2 = max(0.0, min(1.0, float(x2) / max(float(w), 1.0)))
            ny2 = max(0.0, min(1.0, float(y2) / max(float(h), 1.0)))
            detections.append(
                {
                    "label": "person",
                    "confidence": float(confs[i]) if i < len(confs) else 0.0,
                    "nx": nx1,
                    "ny": ny1,
                    "nw": max(0.0, nx2 - nx1),
                    "nh": max(0.0, ny2 - ny1),
                }
            )
    return count, detections


def response(
    trigger: bool,
    score: float,
    label: str,
    payload: dict[str, Any],
    detections: list[dict[str, Any]] | None = None,
) -> str:
    out = {
        "trigger": bool(trigger),
        "score": float(max(0.0, min(1.0, score))),
        "label": label,
        "eventType": "motion",
        "severity": "high" if trigger else "low",
        "payload": payload,
        "detections": detections or [],
    }
    return json.dumps(out, ensure_ascii=True)


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print(response(False, 0.0, "no-input", {"detector": "yolo_person_exit"}))
        return

    req = json.loads(raw)
    camera_id = str(req.get("cameraId", "unknown"))
    rtsp_url = str(req.get("rtspUrl", "")).strip()
    video_path = str(req.get("videoPath", "")).strip()
    offset_sec = max(float(req.get("offsetSec", 0.0) or 0.0), 0.0)
    active_model_path = _resolve_model_path(req)
    person_rule = req.get("personEventRule") if isinstance(req.get("personEventRule"), dict) else {}
    rule_enabled = bool(person_rule.get("enabled", True))
    dwell_sec = max(float(person_rule.get("dwellSec", 5.0)), 1.0)
    rule_event_type = str(person_rule.get("eventType", "person_detected") or "person_detected")
    rule_severity = str(person_rule.get("severity", "high") or "high")
    now = time.time()
    st = load_state(camera_id)

    source_path = video_path or rtsp_url
    if not source_path:
        print(response(False, 0.0, "missing-source", {"reason": "missing_rtsp_or_video_path"}))
        return

    try:
        frame = get_frame(source_path, offset_sec=offset_sec)
        extra = req.get("extra") if isinstance(req.get("extra"), dict) else {}
        rotate_deg = int(req.get("rotationDeg", extra.get("rotationDeg", 0)) or 0)
        if rotate_deg in (90, 180, 270):
            frame = apply_rotation(frame, rotate_deg)
        person_count, detections = detect_person(frame, req)
    except Exception as ex:
        print(
            response(
                False,
                0.0,
                "model-error",
                {
                    "detector": "yolo_person_exit",
                    "reason": str(ex),
                    "hint": "install ultralytics opencv-python and check YOLO_MODEL_PATH",
                },
                [],
            )
        )
        return

    # Person-present dwell trigger mode (default): fire when person is continuously detected for dwellSec.
    if rule_enabled:
        if person_count > 0:
            if st.get("present_start_ts") is None:
                st["present_start_ts"] = now
            present_for = max(0.0, now - float(st["present_start_ts"]))
            if present_for >= dwell_sec:
                st["last_seen_ts"] = now
                st["absent_start_ts"] = None
                st["exit_fired"] = False
                save_state(camera_id, st)
                out = {
                    "trigger": True,
                    "score": float(min(1.0, 0.5 + present_for / max(dwell_sec, 0.1) * 0.5)),
                    "label": "person-dwell",
                    "eventType": rule_event_type,
                    "severity": rule_severity,
                    "detections": detections,
                    "payload": {
                        "detector": "yolo_person_exit",
                        "mode": "person_dwell",
                        "personCount": person_count,
                        "presentForSec": round(present_for, 2),
                        "thresholdSec": dwell_sec,
                    },
                }
                print(json.dumps(out, ensure_ascii=True))
                return
            save_state(camera_id, st)
            print(
                response(
                    False,
                    min(present_for / max(dwell_sec, 0.1), 0.99),
                    "waiting-person-dwell",
                    {
                        "detector": "yolo_person_exit",
                        "mode": "person_dwell",
                        "personCount": person_count,
                        "presentForSec": round(present_for, 2),
                        "thresholdSec": dwell_sec,
                    },
                    detections,
                )
            )
            return
        st["present_start_ts"] = None
        save_state(camera_id, st)
        print(
            response(
                False,
                0.0,
                "no-person",
                {"detector": "yolo_person_exit", "mode": "person_dwell", "personCount": 0},
                [],
            )
        )
        return

    if person_count > 0:
        st["last_seen_ts"] = now
        st["absent_start_ts"] = None
        st["exit_fired"] = False
        save_state(camera_id, st)
        print(
            response(
                False,
                0.1,
                "person-present",
                {"detector": "yolo_person_exit", "personCount": person_count},
                detections,
            )
        )
        return

    # No person detected in this frame.
    if st.get("last_seen_ts") is None:
        # Never seen yet: don't fire.
        save_state(camera_id, st)
        print(
            response(
                False,
                0.05,
                "person-not-seen-yet",
                {"detector": "yolo_person_exit", "personCount": 0},
                [],
            )
        )
        return

    if st.get("absent_start_ts") is None:
        st["absent_start_ts"] = now

    absent_for = max(0.0, now - float(st["absent_start_ts"]))
    should_fire = (not bool(st.get("exit_fired", False))) and (absent_for >= ABSENCE_SEC)
    if should_fire:
        st["exit_fired"] = True
        save_state(camera_id, st)
        score = 0.6 + min(absent_for / max(ABSENCE_SEC, 0.1), 1.0) * 0.4
        print(
            response(
                True,
                score,
                "person-exit",
                {
                    "detector": "yolo_person_exit",
                    "personCount": 0,
                    "absentForSec": round(absent_for, 2),
                    "thresholdSec": ABSENCE_SEC,
                    "modelPath": active_model_path,
                },
                [],
            )
        )
        return

    save_state(camera_id, st)
    print(
        response(
            False,
            min(absent_for / max(ABSENCE_SEC, 0.1), 0.99),
            "waiting-exit-threshold",
            {
                "detector": "yolo_person_exit",
                "personCount": 0,
                "absentForSec": round(absent_for, 2),
                "thresholdSec": ABSENCE_SEC,
            },
            [],
        )
    )


if __name__ == "__main__":
    main()

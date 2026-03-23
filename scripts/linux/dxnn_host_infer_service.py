#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy as np


MODEL_CACHE: dict[str, Any] = {}
INPUT_SHAPE_CACHE: dict[str, tuple[int, int]] = {}


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def load_meta(model_path: str) -> dict[str, Any]:
    meta_path = Path(model_path).with_suffix(".json")
    if not meta_path.exists():
        return {}
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def parse_class_names(meta: dict[str, Any], req: dict[str, Any]) -> list[str]:
    extra = req.get("extra") if isinstance(req.get("extra"), dict) else {}
    raw = extra.get("classNames")
    if isinstance(raw, list):
        names = [str(x).strip() for x in raw if str(x).strip()]
        if names:
            return names
    raw = meta.get("classNames")
    if isinstance(raw, list):
        names = [str(x).strip() for x in raw if str(x).strip()]
        if names:
            return names
    env_raw = os.getenv("DXNN_CLASS_NAMES", "").strip()
    if env_raw:
        return [x.strip() for x in env_raw.split(",") if x.strip()]
    return []


def person_like(label: str) -> bool:
    return label.lower().strip() in {"person", "worker", "human", "signalman"}


def helmet_like(label: str) -> bool:
    return label.lower().strip() in {"helmet", "hardhat", "safety_helmet"}


def head_like(label: str) -> bool:
    return label.lower().strip() in {"head", "bare_head", "no_helmet_head", "helmetless_head"}


def box_xyxy(det: dict[str, Any]) -> tuple[float, float, float, float]:
    x1 = clamp01(float(det.get("nx", 0.0)))
    y1 = clamp01(float(det.get("ny", 0.0)))
    w = clamp01(float(det.get("nw", 0.0)))
    h = clamp01(float(det.get("nh", 0.0)))
    return x1, y1, clamp01(x1 + w), clamp01(y1 + h)


def center_xy(det: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = box_xyxy(det)
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5


def person_has_head(person_det: dict[str, Any], head_dets: list[dict[str, Any]]) -> bool:
    px1, py1, px2, py2 = box_xyxy(person_det)
    for h in head_dets:
        cx, cy = center_xy(h)
        if px1 <= cx <= px2 and py1 <= cy <= py2:
            return True
    return False


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


def point_in_zone(cx: float, cy: float, zone: dict[str, Any]) -> bool:
    shape = str(zone.get("shape", "rect")).lower()
    if shape == "polygon":
        points = zone.get("points") or []
        if not isinstance(points, list):
            return False
        clean: list[dict[str, float]] = []
        for p in points:
            if not isinstance(p, dict):
                continue
            clean.append({"x": clamp01(float(p.get("x", 0.0))), "y": clamp01(float(p.get("y", 0.0)))})
        return point_in_polygon(clamp01(cx), clamp01(cy), clean)
    x = clamp01(float(zone.get("x", 0.0)))
    y = clamp01(float(zone.get("y", 0.0)))
    w = clamp01(float(zone.get("w", 0.0)))
    h = clamp01(float(zone.get("h", 0.0)))
    return x <= cx <= x + w and y <= cy <= y + h


def inside_enabled_roi(cx: float, cy: float, roi: dict[str, Any]) -> bool:
    if not bool(roi.get("enabled", False)):
        return True
    zones = roi.get("zones") or []
    if not isinstance(zones, list) or not zones:
        return True
    valid_zone_count = 0
    for z in zones:
        if not isinstance(z, dict):
            continue
        valid_zone_count += 1
        if point_in_zone(cx, cy, z):
            return True
    return valid_zone_count == 0


def get_engine(model_path: str):
    eng = MODEL_CACHE.get(model_path)
    if eng is not None:
        return eng
    from dx_engine import InferenceEngine, InferenceOption  # type: ignore

    io = InferenceOption()
    io.use_ort = os.getenv("DXNN_USE_ORT", "0").strip().lower() in ("1", "true", "yes", "on")
    io.buffer_count = max(safe_int(os.getenv("DXNN_BUFFER_COUNT", "2"), 2), 1)
    eng = InferenceEngine(model_path, io)
    MODEL_CACHE[model_path] = eng
    return eng


def infer_input_hw(engine, meta: dict[str, Any], req: dict[str, Any], model_path: str) -> tuple[int, int]:
    key = str(model_path)
    cached = INPUT_SHAPE_CACHE.get(key)
    if cached:
        return cached
    extra = req.get("extra") if isinstance(req.get("extra"), dict) else {}
    for src in (extra, meta):
        w = safe_int(src.get("inputWidth", 0), 0)
        h = safe_int(src.get("inputHeight", 0), 0)
        if w > 0 and h > 0:
            INPUT_SHAPE_CACHE[key] = (h, w)
            return (h, w)
    try:
        sizes = engine.get_input_tensor_sizes()
        if sizes and isinstance(sizes, list):
            first = sizes[0]
            if isinstance(first, (list, tuple)) and len(first) >= 4:
                dims = [int(x) for x in first]
                if dims[1] in (1, 3) and dims[2] > 0 and dims[3] > 0:
                    INPUT_SHAPE_CACHE[key] = (dims[2], dims[3])
                    return (dims[2], dims[3])
                if dims[-1] in (1, 3) and dims[1] > 0 and dims[2] > 0:
                    INPUT_SHAPE_CACHE[key] = (dims[1], dims[2])
                    return (dims[1], dims[2])
    except Exception:
        pass
    INPUT_SHAPE_CACHE[key] = (640, 640)
    return (640, 640)


def preprocess(frame: np.ndarray, input_h: int, input_w: int) -> tuple[np.ndarray, dict[str, float]]:
    h, w = frame.shape[:2]
    r = min(input_w / max(w, 1), input_h / max(h, 1))
    nw, nh = int(round(w * r)), int(round(h * r))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((input_h, input_w, 3), 114, dtype=np.uint8)
    dw = (input_w - nw) // 2
    dh = (input_h - nh) // 2
    canvas[dh : dh + nh, dw : dw + nw] = resized
    x = canvas.astype(np.float32) / 255.0
    x = np.transpose(x, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(x), {"scale": r, "dw": float(dw), "dh": float(dh), "src_w": float(w), "src_h": float(h)}


def preprocess_for_engine(frame: np.ndarray, input_h: int, input_w: int, engine) -> tuple[np.ndarray, dict[str, float]]:
    h, w = frame.shape[:2]
    r = min(input_w / max(w, 1), input_h / max(h, 1))
    nw, nh = int(round(w * r)), int(round(h * r))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((input_h, input_w, 3), 114, dtype=np.uint8)
    dw = (input_w - nw) // 2
    dh = (input_h - nh) // 2
    canvas[dh : dh + nh, dw : dw + nw] = resized

    x: np.ndarray
    expected_nhwc_uint8 = False
    try:
        infos = engine.get_input_tensors_info()
        if isinstance(infos, list) and infos:
            info = infos[0] if isinstance(infos[0], dict) else {}
            shp = info.get("shape", [])
            dt = info.get("dtype")
            dtype_name = getattr(dt, "__name__", str(dt)).lower()
            expected_nhwc_uint8 = (
                isinstance(shp, (list, tuple))
                and len(shp) == 4
                and int(shp[-1]) in (1, 3)
                and int(shp[1]) > 1
                and int(shp[2]) > 1
                and "uint8" in dtype_name
            )
    except Exception:
        expected_nhwc_uint8 = False

    if expected_nhwc_uint8:
        x = canvas[None, ...]
    else:
        x = canvas.astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None, ...]

    return np.ascontiguousarray(x), {"scale": r, "dw": float(dw), "dh": float(dh), "src_w": float(w), "src_h": float(h)}


def select_output_tensor(outputs: list[np.ndarray]) -> np.ndarray:
    if not outputs:
        raise RuntimeError("dxnn_no_outputs")
    rank3 = [o for o in outputs if isinstance(o, np.ndarray) and o.ndim == 3]
    if rank3:
        return rank3[0]
    return outputs[0]


def decode_yolo_like(
    out: np.ndarray,
    class_names: list[str],
    conf_thres: float,
    roi: dict[str, Any],
    letterbox: dict[str, float],
) -> list[dict[str, Any]]:
    arr = np.asarray(out)
    arr = np.squeeze(arr)
    if arr.ndim == 1:
        return []
    if arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1) if arr.shape[0] < arr.shape[-1] else arr.reshape(-1, arr.shape[-1])
    if arr.ndim == 2 and arr.shape[0] <= 256 and arr.shape[1] > arr.shape[0]:
        arr = arr.transpose(1, 0)
    if arr.ndim != 2 or arr.shape[1] < 6:
        return []

    src_w = max(letterbox.get("src_w", 1.0), 1.0)
    src_h = max(letterbox.get("src_h", 1.0), 1.0)
    scale = max(letterbox.get("scale", 1.0), 1e-6)
    dw = letterbox.get("dw", 0.0)
    dh = letterbox.get("dh", 0.0)
    detections: list[dict[str, Any]] = []
    for row in arr:
        x, y, w, h = [float(v) for v in row[:4]]
        tail = row[4:]
        if tail.shape[0] < 2:
            continue
        if tail.shape[0] >= 3:
            obj = float(tail[0])
            cls_scores = tail[1:]
            cls_idx = int(np.argmax(cls_scores))
            cls_conf = float(cls_scores[cls_idx])
            score = obj * cls_conf if obj <= 1.0 else cls_conf
        else:
            cls_idx = int(np.argmax(tail))
            score = float(tail[cls_idx])
        if score < conf_thres:
            continue
        x1 = x - w * 0.5
        y1 = y - h * 0.5
        x2 = x + w * 0.5
        y2 = y + h * 0.5
        x1 = (x1 - dw) / scale
        y1 = (y1 - dh) / scale
        x2 = (x2 - dw) / scale
        y2 = (y2 - dh) / scale
        x1 = max(0.0, min(src_w, x1))
        y1 = max(0.0, min(src_h, y1))
        x2 = max(0.0, min(src_w, x2))
        y2 = max(0.0, min(src_h, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        nx1 = clamp01(x1 / src_w)
        ny1 = clamp01(y1 / src_h)
        nx2 = clamp01(x2 / src_w)
        ny2 = clamp01(y2 / src_h)
        cx = (nx1 + nx2) * 0.5
        cy = (ny1 + ny2) * 0.5
        label = class_names[cls_idx] if cls_idx < len(class_names) else f"class_{cls_idx}"
        detections.append(
            {
                "label": label,
                "confidence": float(score),
                "nx": nx1,
                "ny": ny1,
                "nw": max(0.0, nx2 - nx1),
                "nh": max(0.0, ny2 - ny1),
            }
        )
    return detections


def _build_anchor_points(input_h: int, input_w: int, strides: tuple[int, ...] = (8, 16, 32)) -> tuple[np.ndarray, np.ndarray]:
    points: list[np.ndarray] = []
    stride_vec: list[np.ndarray] = []
    for s in strides:
        gh = max(int(input_h // s), 1)
        gw = max(int(input_w // s), 1)
        ys = np.arange(gh, dtype=np.float32) + 0.5
        xs = np.arange(gw, dtype=np.float32) + 0.5
        yy, xx = np.meshgrid(ys, xs, indexing="ij")
        p = np.stack([xx.reshape(-1), yy.reshape(-1)], axis=1)
        points.append(p)
        stride_vec.append(np.full((p.shape[0], 1), float(s), dtype=np.float32))
    if not points:
        return np.zeros((0, 2), dtype=np.float32), np.zeros((0, 1), dtype=np.float32)
    return np.concatenate(points, axis=0), np.concatenate(stride_vec, axis=0)


def _nms_class_agnostic(dets: list[dict[str, Any]], iou_thres: float = 0.5, max_det: int = 200) -> list[dict[str, Any]]:
    if not dets:
        return []
    boxes = np.array(
        [[float(d["nx"]), float(d["ny"]), float(d["nx"]) + float(d["nw"]), float(d["ny"]) + float(d["nh"])] for d in dets],
        dtype=np.float32,
    )
    scores = np.array([float(d.get("confidence", 0.0)) for d in dets], dtype=np.float32)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0 and len(keep) < max_det:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        area_i = max((boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1]), 1e-9)
        area_r = np.maximum((boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1]), 1e-9)
        iou = inter / (area_i + area_r - inter + 1e-9)
        order = rest[iou < iou_thres]
    return [dets[i] for i in keep]


def _nms_per_label(dets: list[dict[str, Any]], iou_thres: float = 0.5, max_det_per_label: int = 100) -> list[dict[str, Any]]:
    if not dets:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for d in dets:
        grouped.setdefault(str(d.get("label", "")), []).append(d)
    out: list[dict[str, Any]] = []
    for _, items in grouped.items():
        out.extend(_nms_class_agnostic(items, iou_thres=iou_thres, max_det=max_det_per_label))
    out.sort(key=lambda x: float(x.get("confidence", 0.0)), reverse=True)
    return out


def decode_split_box_cls(
    box_out: np.ndarray,
    cls_out: np.ndarray,
    class_names: list[str],
    conf_thres: float,
    roi: dict[str, Any],
    letterbox: dict[str, float],
    input_h: int,
    input_w: int,
) -> list[dict[str, Any]]:
    b = np.asarray(box_out)
    c = np.asarray(cls_out)
    b = np.squeeze(b)
    c = np.squeeze(c)

    # Normalize box tensor to [N, 4]
    if b.ndim == 3 and b.shape[0] == 1:
        b = b[0]
    if b.ndim == 2 and b.shape[0] == 4:
        b = b.transpose(1, 0)
    if b.ndim == 3 and b.shape[0] == 4:
        b = b.reshape(4, -1).transpose(1, 0)
    if b.ndim != 2 or b.shape[1] != 4:
        return []

    # Normalize cls tensor to [N, C]
    if c.ndim == 1:
        return []
    if c.ndim == 2:
        if c.shape[0] <= 32 and c.shape[1] > c.shape[0]:
            c = c.transpose(1, 0)
    elif c.ndim == 3:
        if c.shape[0] <= 32:
            c = c.reshape(c.shape[0], -1).transpose(1, 0)
        else:
            c = c.reshape(-1, c.shape[-1])
    if c.ndim != 2:
        return []

    n = min(b.shape[0], c.shape[0])
    if n <= 0:
        return []
    b = b[:n]
    c = c[:n]

    # If no metadata exists for this 3-class helmet model, use practical defaults.
    if not class_names and c.shape[1] == 3:
        class_names = ["person", "head", "helmet"]

    # Keep GUI/user threshold as the default behavior.
    # Compatibility escape hatch: relax threshold only when explicitly enabled.
    cls_max = float(np.max(c)) if c.size else 0.0
    eff_conf = conf_thres
    relax_low_conf = os.getenv("DXNN_RELAX_LOW_CLASS_CONF", "false").strip().lower() in ("1", "true", "yes", "on")
    if relax_low_conf and cls_max < 0.05:
        eff_conf = min(conf_thres, 0.001)

    src_w = max(letterbox.get("src_w", 1.0), 1.0)
    src_h = max(letterbox.get("src_h", 1.0), 1.0)
    scale = max(letterbox.get("scale", 1.0), 1e-6)
    dw = letterbox.get("dw", 0.0)
    dh = letterbox.get("dh", 0.0)

    decode_mode = os.getenv("DXNN_SPLIT_DECODE_MODE", "ltrb").strip().lower()
    anchors, strides = _build_anchor_points(input_h, input_w)
    use_ltrb = decode_mode != "xywh_stride" and anchors.shape[0] >= n

    detections: list[dict[str, Any]] = []
    for i in range(n):
        row_cls = c[i]
        cls_idx = int(np.argmax(row_cls))
        score = float(row_cls[cls_idx])
        if score < eff_conf:
            continue
        if use_ltrb:
            l, t, r, btm = [float(v) for v in b[i]]
            ax = float(anchors[i, 0])
            ay = float(anchors[i, 1])
            st = float(strides[i, 0])
            # YOLOv8 DFL output: [l, t, r, b] distances in grid units.
            x1 = (ax - l) * st
            y1 = (ay - t) * st
            x2 = (ax + r) * st
            y2 = (ay + btm) * st
        else:
            # Fallback for models that emit xywh.
            cx, cy, bw, bh = [float(v) for v in b[i]]
            st = float(strides[i, 0]) if strides.shape[0] > i else 1.0
            cx *= st
            cy *= st
            bw *= st
            bh *= st
            if bw <= 0.0 or bh <= 0.0:
                continue
            x1 = cx - bw * 0.5
            y1 = cy - bh * 0.5
            x2 = cx + bw * 0.5
            y2 = cy + bh * 0.5

        x1 = (x1 - dw) / scale
        y1 = (y1 - dh) / scale
        x2 = (x2 - dw) / scale
        y2 = (y2 - dh) / scale
        x1 = max(0.0, min(src_w, x1))
        y1 = max(0.0, min(src_h, y1))
        x2 = max(0.0, min(src_w, x2))
        y2 = max(0.0, min(src_h, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        nx1 = clamp01(x1 / src_w)
        ny1 = clamp01(y1 / src_h)
        nx2 = clamp01(x2 / src_w)
        ny2 = clamp01(y2 / src_h)
        label = class_names[cls_idx] if cls_idx < len(class_names) else f"class_{cls_idx}"
        detections.append(
            {
                "label": label,
                "confidence": score,
                "nx": nx1,
                "ny": ny1,
                "nw": max(0.0, nx2 - nx1),
                "nh": max(0.0, ny2 - ny1),
            }
        )
    return _nms_per_label(detections, iou_thres=0.5, max_det_per_label=80)


def decode_model_outputs(
    outputs: list[np.ndarray],
    class_names: list[str],
    conf_thres: float,
    roi: dict[str, Any],
    letterbox: dict[str, float],
    input_h: int,
    input_w: int,
) -> list[dict[str, Any]]:
    if not outputs:
        return []
    if len(outputs) >= 2:
        a = np.asarray(outputs[0])
        b = np.asarray(outputs[1])
        if a.size > 0 and b.size > 0:
            sa = tuple(a.shape)
            sb = tuple(b.shape)
            if sa[-1] == sb[-1] and sa[-1] >= 1000 and (4 in sa or (len(sa) >= 2 and sa[-2] == 4)):
                dets = decode_split_box_cls(a, b, class_names, conf_thres, roi, letterbox, input_h, input_w)
                if dets:
                    return dets
    return decode_yolo_like(select_output_tensor(outputs), class_names, conf_thres, roi, letterbox)


def capture_frame(source_path: str, timeout_sec: float, offset_sec: float = 0.0) -> np.ndarray:
    cap = cv2.VideoCapture(source_path, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("video_capture_open_failed")
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if offset_sec > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(offset_sec, 0.0) * 1000.0)
        start = cv2.getTickCount()
        freq = cv2.getTickFrequency()
        while True:
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame
            if (cv2.getTickCount() - start) / max(freq, 1.0) > timeout_sec:
                break
        raise RuntimeError("video_capture_read_timeout")
    finally:
        cap.release()


def infer(req: dict[str, Any]) -> dict[str, Any]:
    model_path = str(req.get("modelPath", "")).strip() or os.getenv("DXNN_MODEL_PATH", "").strip()
    map_from = os.getenv("HOST_MODEL_MAP_FROM", "/opt/vms/models").strip()
    map_to = os.getenv("HOST_MODEL_MAP_TO", "/home/recomputer/vms-8ch-webrtc/models").strip()
    if model_path and map_from and map_to and model_path.startswith(map_from):
        model_path = map_to + model_path[len(map_from) :]
    rtsp_url = str(req.get("rtspUrl", "")).strip()
    video_path = str(req.get("videoPath", "")).strip()
    offset_sec = max(safe_float(req.get("offsetSec", 0.0), 0.0), 0.0)
    conf_thres = clamp01(safe_float(req.get("confidenceThreshold", 0.35), 0.35))
    roi = req.get("roi") if isinstance(req.get("roi"), dict) else {"enabled": False, "zones": []}
    event_type = str(req.get("eventType", os.getenv("DXNN_EVENT_TYPE", "helmet_missing"))).strip() or "helmet_missing"
    severity = str(req.get("severity", os.getenv("DXNN_EVENT_SEVERITY", "high"))).strip() or "high"
    timeout_sec = max(safe_float(os.getenv("DXNN_FRAME_TIMEOUT_SEC", "4.0"), 4.0), 1.0)

    if not model_path:
        return {"trigger": False, "score": 0.0, "label": "model-error", "eventType": event_type, "severity": severity, "payload": {"reason": "missing_model_path"}, "detections": []}
    source_path = video_path or rtsp_url
    if not source_path:
        return {"trigger": False, "score": 0.0, "label": "model-error", "eventType": event_type, "severity": severity, "payload": {"reason": "missing_rtsp_or_video_path"}, "detections": []}
    try:
        meta = load_meta(model_path)
        class_names = parse_class_names(meta, req)
        engine = get_engine(model_path)
        input_h, input_w = infer_input_hw(engine, meta, req, model_path)
        frame = capture_frame(source_path, timeout_sec, offset_sec=offset_sec)
        input_tensor, letterbox = preprocess_for_engine(frame, input_h, input_w, engine)
        outputs = engine.run([input_tensor])
        detections = decode_model_outputs(outputs, class_names, conf_thres, roi, letterbox, input_h, input_w)
        people = [d for d in detections if person_like(str(d.get("label", "")))]
        heads = [d for d in detections if head_like(str(d.get("label", "")))]
        helmets = [d for d in detections if helmet_like(str(d.get("label", "")))]
        people_with_head = [p for p in people if person_has_head(p, heads)]
        trigger_by_head = len(people_with_head) > 0 and len(helmets) == 0
        trigger_by_person_only = len(heads) == 0 and len(people) > 0 and len(helmets) == 0
        trigger = bool(trigger_by_head or trigger_by_person_only)
        payload = {
            "detector": "dxnn_host_infer_service",
            "modelPath": model_path,
            "personCount": len(people),
            "headCount": len(heads),
            "personWithHeadCount": len(people_with_head),
            "helmetCount": len(helmets),
            "fallbackNoHead": bool(trigger_by_person_only),
            "totalDetections": len(detections),
        }
        score = 0.99 if trigger else (0.2 if people_with_head else 0.01)
        label = "helmet-missing" if trigger else ("helmet-present" if helmets else "no-person-or-head")
        return {
            "trigger": trigger,
            "score": score,
            "label": label,
            "eventType": event_type,
            "severity": severity,
            "payload": payload,
            "detections": detections,
        }
    except Exception as ex:
        return {
            "trigger": False,
            "score": 0.0,
            "label": "model-error",
            "eventType": event_type,
            "severity": severity,
            "payload": {"reason": str(ex), "detector": "dxnn_host_infer_service"},
            "detections": [],
        }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict[str, Any]) -> None:
        body = json.dumps(obj, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/infer":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(max(length, 0)).decode("utf-8", errors="ignore")
            req = json.loads(raw) if raw else {}
            if not isinstance(req, dict):
                req = {}
            out = infer(req)
            self._json(200, out)
        except Exception as ex:
            self._json(500, {"trigger": False, "label": "model-error", "payload": {"reason": str(ex)}})


def main() -> None:
    bind = os.getenv("HOST_DXNN_BIND", "0.0.0.0")
    port = int(os.getenv("HOST_DXNN_PORT", "18081"))
    server = ThreadingHTTPServer((bind, port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()

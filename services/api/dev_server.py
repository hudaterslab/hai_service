import json
import os
import time
import base64
import sqlite3
import uuid
import ipaddress
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from urllib.parse import urlparse as urlparse_std
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "services" / "api" / "app" / "static"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "dev.db"
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")
RTSP_FALLBACK_PATH = os.getenv("DEV_AI_RTSP_FALLBACK_PATH", "/Streaming/Channels/101")

_YOLO_MODEL = None
_YOLO_MODEL_SOURCE = ""


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
              id TEXT PRIMARY KEY, name TEXT NOT NULL, rtsp_url TEXT NOT NULL, onvif_profile TEXT,
              webrtc_path TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'offline',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_policies (
              id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, event_type TEXT NOT NULL, mode TEXT NOT NULL,
              clip_pre_sec INTEGER NOT NULL DEFAULT 10, clip_post_sec INTEGER NOT NULL DEFAULT 20,
              clip_cooldown_sec INTEGER NOT NULL DEFAULT 5, clip_merge_window_sec INTEGER NOT NULL DEFAULT 3,
              snapshot_count INTEGER NOT NULL DEFAULT 1, snapshot_interval_ms INTEGER NOT NULL DEFAULT 0,
              snapshot_format TEXT NOT NULL DEFAULT 'jpg', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE (camera_id, event_type)
            );
            CREATE TABLE IF NOT EXISTS destinations (
              id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              config_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routing_rules (
              id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, event_type TEXT NOT NULL, artifact_kind TEXT NOT NULL DEFAULT 'both',
              destination_id TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, event_type TEXT NOT NULL, severity TEXT NOT NULL,
              occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS camera_rois (
              camera_id TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL DEFAULT 0,
              zones_json TEXT NOT NULL DEFAULT '[]',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, event_id TEXT NOT NULL, camera_id TEXT NOT NULL, kind TEXT NOT NULL,
              local_path TEXT NOT NULL, uri TEXT, mime_type TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recorder_camera_health (
              camera_id TEXT PRIMARY KEY,
              connected INTEGER NOT NULL DEFAULT 0,
              last_connect_reason TEXT,
              ring_running INTEGER NOT NULL DEFAULT 0,
              ring_restart_count INTEGER NOT NULL DEFAULT 0,
              last_ring_exit_code INTEGER,
              last_probe_at TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        cur = c.execute("SELECT key FROM app_settings WHERE key = 'ai_model'")
        if not cur.fetchone():
            c.execute(
                "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (
                    "ai_model",
                    json.dumps({"enabled": False, "modelPath": "", "timeoutSec": 5, "pollSec": 2, "cooldownSec": 10}),
                    now_iso(),
                ),
            )
        cur = c.execute("SELECT key FROM app_settings WHERE key = 'person_event_rule'")
        if not cur.fetchone():
            c.execute(
                "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (
                    "person_event_rule",
                    json.dumps({"enabled": True, "dwellSec": 5, "cooldownSec": 10, "eventType": "person_detected", "severity": "high"}),
                    now_iso(),
                ),
            )
        c.commit()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload):
        b = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _text(self, code: int, payload: str, ctype="text/plain; charset=utf-8"):
        b = payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def _candidate_rtsp_urls(self, ip: str, username: str, password: str, ports: list[int]) -> list[str]:
        auth = ""
        if username:
            auth = f"{quote(username)}:{quote(password)}@"
        paths = [
            "/Streaming/Channels/101",
            "/Streaming/Channels/102",
            "/ISAPI/Streaming/channels/101",
            "/h264/ch1/main/av_stream",
            "/h264/ch1/sub/av_stream",
            "/live/ch00_0",
            "/live",
            "/h264",
            "/stream1",
            "/cam/realmonitor?channel=1&subtype=0",
        ]
        urls = []
        for p in ports:
            for path in paths:
                urls.append(f"rtsp://{auth}{ip}:{p}{path}")
        return urls

    def _probe_rtsp(self, url: str, timeout_sec: float) -> tuple[bool, str]:
        try:
            parsed = urlparse_std(url)
            if parsed.scheme.lower() != "rtsp" or not parsed.hostname:
                return False, "invalid_scheme"
            host = parsed.hostname
            port = parsed.port or 554
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            request_uri = f"rtsp://{host}:{port}{path}"
            with socket.create_connection((host, port), timeout=timeout_sec) as s:
                s.settimeout(timeout_sec)
                req = (
                    f"DESCRIBE {request_uri} RTSP/1.0\r\n"
                    "CSeq: 1\r\n"
                    "User-Agent: vms-discover\r\n"
                    "Accept: application/sdp\r\n\r\n"
                )
                s.sendall(req.encode("utf-8"))
                resp = s.recv(512).decode("utf-8", errors="ignore")
                line = resp.splitlines()[0] if resp else ""
                if not line.startswith("RTSP/1.0"):
                    return False, "no_rtsp_banner"
                parts = line.split()
                code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
                if code in (200, 401, 403):
                    return True, line[:120]
                if code in (404, 454):
                    return False, f"path_invalid:{code}"
                return False, f"rtsp_status:{code}"
        except Exception as ex:
            return False, str(ex)

    def _scan_host(self, ip: str, username: str, password: str, ports: list[int], timeout_sec: float) -> dict:
        for url in self._candidate_rtsp_urls(ip, username, password, ports):
            ok, detail = self._probe_rtsp(url, timeout_sec)
            if ok:
                return {"ip": ip, "found": True, "rtspUrl": url, "detail": detail}
        return {"ip": ip, "found": False, "rtspUrl": None, "detail": "no_rtsp_response"}

    def _onvif_probe_xml(self) -> str:
        msg_id = f"uuid:{uuid.uuid4()}"
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>{msg_id}</w:MessageID>
    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""

    def _onvif_discover(self, timeout_ms: int) -> list[dict]:
        payload = self._onvif_probe_xml().encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(max(timeout_ms, 300) / 1000.0)
        try:
            for _ in range(2):
                sock.sendto(payload, ("239.255.255.250", 3702))
        except Exception:
            sock.close()
            return []
        ns = {
            "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
            "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
        }
        end_ts = datetime.now(timezone.utc).timestamp() + max(timeout_ms, 300) / 1000.0
        devices = {}
        while datetime.now(timezone.utc).timestamp() < end_ts:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except Exception:
                continue
            ip = addr[0]
            xaddrs = []
            urn = None
            types = ""
            try:
                root = ET.fromstring(data.decode("utf-8", errors="ignore"))
                xa = root.find(".//d:ProbeMatch/d:XAddrs", ns)
                if xa is not None and xa.text:
                    xaddrs = [x.strip() for x in xa.text.split() if x.strip()]
                    for x in xaddrs:
                        try:
                            pu = urlparse_std(x)
                            if pu.hostname:
                                ip = pu.hostname
                                break
                        except Exception:
                            pass
                aid = root.find(".//d:ProbeMatch/a:EndpointReference/a:Address", ns)
                if aid is not None and aid.text:
                    urn = aid.text.strip()
                typ = root.find(".//d:ProbeMatch/d:Types", ns)
                if typ is not None and typ.text:
                    types = typ.text.strip()
            except Exception:
                pass
            devices[ip] = {"ip": ip, "xaddrs": xaddrs, "urn": urn, "types": types}
        sock.close()
        return sorted(devices.values(), key=lambda x: x["ip"])

    def _auto_cidr_candidates(self, full_scan: bool = False) -> list[str]:
        out = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            parts = ip.split(".")
            if len(parts) == 4:
                out.append(f"{parts[0]}.{parts[1]}.{parts[2]}.0/24")
        except Exception:
            pass
        out.extend(
            [
                "192.168.1.0/24",
                "192.168.0.0/24",
                "192.168.10.0/24",
                "10.0.0.0/24",
                "10.0.1.0/24",
                "10.0.2.0/24",
                "172.16.0.0/24",
            ]
        )
        if full_scan:
            out.extend([f"192.168.{i}.0/24" for i in range(0, 256)])
            out.extend([f"10.0.{i}.0/24" for i in range(0, 256)])
            out.extend([f"172.{i}.0.0/24" for i in range(16, 32)])
        uniq = []
        seen = set()
        for c in out:
            if c not in seen:
                uniq.append(c)
                seen.add(c)
        return uniq

    def _normalize_rtsp_url(self, rtsp_url: str) -> str:
        if not rtsp_url:
            return rtsp_url
        if rtsp_url.startswith("rtsp://") and rtsp_url.count("/") <= 2:
            return rtsp_url.rstrip("/") + RTSP_FALLBACK_PATH
        return rtsp_url

    def _list_model_candidates(self) -> list[dict]:
        exts = {".pt", ".onnx", ".engine", ".py", ".exe"}
        roots = [ROOT / "models", ROOT]
        out = []
        seen = set()
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in exts:
                    continue
                key = str(p.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "path": str(p.resolve()),
                        "name": p.name,
                        "ext": p.suffix.lower(),
                        "source": "models" if (ROOT / "models") in p.parents else "project",
                    }
                )
        out.sort(key=lambda x: (x["source"], x["name"].lower(), x["path"].lower()))
        return out

    def _read_rtsp_frame(self, rtsp_url: str, timeout_sec: float = 4.0, max_tries: int = 20):
        import cv2  # type: ignore

        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError("video_capture_open_failed")
        try:
            started = time.time()
            for _ in range(max(max_tries, 1)):
                ok, frame = cap.read()
                if ok and frame is not None:
                    return frame
                if (time.time() - started) >= max(timeout_sec, 0.5):
                    break
                time.sleep(0.05)
        finally:
            cap.release()
        raise RuntimeError("video_capture_read_timeout")

    def _load_yolo(self):
        global _YOLO_MODEL, _YOLO_MODEL_SOURCE
        if _YOLO_MODEL is not None and _YOLO_MODEL_SOURCE == YOLO_MODEL_PATH:
            return _YOLO_MODEL
        from ultralytics import YOLO  # type: ignore

        _YOLO_MODEL = YOLO(YOLO_MODEL_PATH)
        _YOLO_MODEL_SOURCE = YOLO_MODEL_PATH
        return _YOLO_MODEL

    def _detect_person_boxes(self, frame, roi: dict, conf_thres: float):
        model = self._load_yolo()
        result = model.predict(
            source=frame,
            conf=conf_thres,
            verbose=False,
            device=YOLO_DEVICE,
        )[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        cls = boxes.cls.tolist() if getattr(boxes, "cls", None) is not None else []
        confs = boxes.conf.tolist() if getattr(boxes, "conf", None) is not None else []
        xyxy = boxes.xyxy.tolist() if getattr(boxes, "xyxy", None) is not None else []
        names = getattr(result, "names", {}) or {}
        h, w = frame.shape[:2]

        roi_enabled = bool((roi or {}).get("enabled", False))
        zones = (roi or {}).get("zones") or []

        def inside_roi(cx: float, cy: float) -> bool:
            if not roi_enabled or not zones:
                return True
            for z in zones:
                x = float(z.get("x", 0.0))
                y = float(z.get("y", 0.0))
                rw = float(z.get("w", 0.0))
                rh = float(z.get("h", 0.0))
                if x <= cx <= x + rw and y <= cy <= y + rh:
                    return True
            return False

        out = []
        for i, c in enumerate(cls):
            class_id = int(c)
            if class_id != 0:
                continue
            if i >= len(xyxy):
                continue
            x1, y1, x2, y2 = xyxy[i]
            px1 = int(max(0, min(w - 1, x1)))
            py1 = int(max(0, min(h - 1, y1)))
            px2 = int(max(0, min(w - 1, x2)))
            py2 = int(max(0, min(h - 1, y2)))
            if px2 <= px1 or py2 <= py1:
                continue
            cx = ((px1 + px2) * 0.5) / max(float(w), 1.0)
            cy = ((py1 + py2) * 0.5) / max(float(h), 1.0)
            if not inside_roi(cx, cy):
                continue
            out.append(
                {
                    "classId": class_id,
                    "label": str(names.get(class_id, "person")),
                    "confidence": float(confs[i]) if i < len(confs) else 0.0,
                    "x1": px1,
                    "y1": py1,
                    "x2": px2,
                    "y2": py2,
                    "nx": px1 / max(float(w), 1.0),
                    "ny": py1 / max(float(h), 1.0),
                    "nw": (px2 - px1) / max(float(w), 1.0),
                    "nh": (py2 - py1) / max(float(h), 1.0),
                }
            )
        return out

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            return self._text(200, html, "text/html; charset=utf-8")
        if p.path.startswith("/static/"):
            file_path = STATIC_DIR / p.path.removeprefix("/static/")
            if not file_path.exists():
                return self._text(404, "not found")
            ctype = "text/plain; charset=utf-8"
            if file_path.suffix == ".js":
                ctype = "application/javascript; charset=utf-8"
            elif file_path.suffix == ".css":
                ctype = "text/css; charset=utf-8"
            elif file_path.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            return self._text(200, file_path.read_text(encoding="utf-8"), ctype)
        if p.path == "/healthz":
            return self._json(200, {"ok": True, "mode": "sqlite-dev", "db": str(DB_PATH), "serverVersion": "20260303-cam-settings"})
        if p.path == "/auth/me":
            return self._json(200, {"username": "dev", "role": "admin", "authEnabled": False})

        if p.path == "/cameras":
            with conn() as c:
                rows = c.execute("SELECT * FROM cameras ORDER BY created_at DESC").fetchall()
            return self._json(
                200,
                [
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
                ],
            )
        if p.path == "/event-policies":
            with conn() as c:
                rows = c.execute("SELECT * FROM event_policies ORDER BY updated_at DESC").fetchall()
            return self._json(
                200,
                [
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
                ],
            )
        if p.path.startswith("/cameras/") and p.path.endswith("/roi"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            with conn() as c:
                row = c.execute("SELECT * FROM camera_rois WHERE camera_id = ?", (cam_id,)).fetchone()
            if not row:
                return self._json(200, {"cameraId": cam_id, "enabled": False, "zones": []})
            return self._json(
                200,
                {
                    "cameraId": row["camera_id"],
                    "enabled": bool(row["enabled"]),
                    "zones": json.loads(row["zones_json"] or "[]"),
                },
            )
        if p.path.startswith("/cameras/") and p.path.endswith("/model-settings"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            key = f"camera_model:{cam_id}"
            with conn() as c:
                row = c.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
            if not row:
                return self._json(
                    200,
                    {
                        "enabled": False,
                        "modelPath": "",
                        "confidenceThreshold": 0.35,
                        "timeoutSec": 5,
                        "pollSec": 2,
                        "cooldownSec": 10,
                        "extra": {},
                    },
                )
            try:
                cfg = json.loads(row["value_json"] or "{}")
            except Exception:
                cfg = {}
            return self._json(
                200,
                {
                    "enabled": bool(cfg.get("enabled", False)),
                    "modelPath": str(cfg.get("modelPath", "") or ""),
                    "confidenceThreshold": float(cfg.get("confidenceThreshold", 0.35)),
                    "timeoutSec": int(cfg.get("timeoutSec", 5)),
                    "pollSec": float(cfg.get("pollSec", 2)),
                    "cooldownSec": int(cfg.get("cooldownSec", 10)),
                    "extra": cfg.get("extra", {}) if isinstance(cfg.get("extra", {}), dict) else {},
                },
            )
        if p.path == "/settings/ai-model":
            with conn() as c:
                row = c.execute("SELECT value_json FROM app_settings WHERE key='ai_model'").fetchone()
            return self._json(200, json.loads(row["value_json"]))
        if p.path == "/settings/person-event":
            with conn() as c:
                row = c.execute("SELECT value_json FROM app_settings WHERE key='person_event_rule'").fetchone()
            if not row:
                return self._json(200, {"enabled": True, "dwellSec": 5, "cooldownSec": 10, "eventType": "person_detected", "severity": "high"})
            return self._json(200, json.loads(row["value_json"]))
        if p.path == "/models/list":
            with conn() as c:
                row = c.execute("SELECT value_json FROM app_settings WHERE key='ai_model'").fetchone()
            selected_path = ""
            if row:
                try:
                    selected_path = str((json.loads(row["value_json"] or "{}")).get("modelPath", "") or "")
                except Exception:
                    selected_path = ""
            return self._json(
                200,
                {
                    "selectedPath": selected_path,
                    "items": self._list_model_candidates(),
                },
            )
        if p.path == "/dev/ai/preview":
            qq = parse_qs(p.query)
            camera_id = (qq.get("cameraId") or [""])[0].strip()
            conf_thres = 0.35
            try:
                conf_thres = float((qq.get("conf") or ["0.35"])[0])
            except Exception:
                conf_thres = 0.35
            conf_thres = max(0.05, min(conf_thres, 0.95))
            if not camera_id:
                return self._json(400, {"detail": "cameraId is required"})
            with conn() as c:
                cam = c.execute(
                    "SELECT id, name, rtsp_url, status, webrtc_path FROM cameras WHERE id = ?",
                    (camera_id,),
                ).fetchone()
                if not cam:
                    return self._json(404, {"detail": "camera not found"})
                roi_row = c.execute(
                    "SELECT enabled, zones_json FROM camera_rois WHERE camera_id = ?",
                    (camera_id,),
                ).fetchone()
            roi = {"enabled": False, "zones": []}
            if roi_row:
                try:
                    roi = {
                        "enabled": bool(roi_row["enabled"]),
                        "zones": json.loads(roi_row["zones_json"] or "[]"),
                    }
                except Exception:
                    roi = {"enabled": bool(roi_row["enabled"]), "zones": []}

            rtsp_url = self._normalize_rtsp_url(str(cam["rtsp_url"]))
            try:
                frame = self._read_rtsp_frame(rtsp_url)
                detections = self._detect_person_boxes(frame, roi, conf_thres)
            except Exception as ex:
                return self._json(
                    500,
                    {
                        "detail": "ai_preview_failed",
                        "error": str(ex),
                        "cameraId": camera_id,
                        "hint": "install ultralytics opencv-python and verify YOLO_MODEL_PATH/RTSP",
                    },
                )

            import cv2  # type: ignore

            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                return self._json(500, {"detail": "jpeg_encode_failed"})
            b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
            return self._json(
                200,
                {
                    "cameraId": str(cam["id"]),
                    "cameraName": cam["name"],
                    "status": cam["status"],
                    "webrtcPath": cam["webrtc_path"],
                    "rtspUrl": rtsp_url,
                    "capturedAt": now_iso(),
                    "modelPath": YOLO_MODEL_PATH,
                    "confThreshold": conf_thres,
                    "count": len(detections),
                    "roi": roi,
                    "detections": detections,
                    "imageDataUrl": f"data:image/jpeg;base64,{b64}",
                },
            )
        if p.path == "/destinations":
            with conn() as c:
                rows = c.execute("SELECT * FROM destinations ORDER BY created_at DESC").fetchall()
            return self._json(
                200,
                [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "type": r["type"],
                        "enabled": bool(r["enabled"]),
                        "config": json.loads(r["config_json"]),
                    }
                    for r in rows
                ],
            )
        if p.path == "/routing-rules":
            with conn() as c:
                rows = c.execute("SELECT * FROM routing_rules ORDER BY created_at DESC").fetchall()
            return self._json(
                200,
                [
                    {
                        "id": r["id"],
                        "cameraId": r["camera_id"],
                        "eventType": r["event_type"],
                        "artifactKind": r["artifact_kind"],
                        "destinationId": r["destination_id"],
                        "enabled": bool(r["enabled"]),
                    }
                    for r in rows
                ],
            )
        if p.path == "/events":
            q = "SELECT * FROM events"
            params = []
            qq = parse_qs(p.query)
            clauses = []
            if qq.get("cameraId"):
                clauses.append("camera_id = ?")
                params.append(qq["cameraId"][0])
            if qq.get("type"):
                clauses.append("event_type = ?")
                params.append(qq["type"][0])
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY occurred_at DESC LIMIT 200"
            with conn() as c:
                rows = c.execute(q, params).fetchall()
            return self._json(
                200,
                [
                    {
                        "id": r["id"],
                        "cameraId": r["camera_id"],
                        "type": r["event_type"],
                        "severity": r["severity"],
                        "occurredAt": r["occurred_at"],
                        "payload": json.loads(r["payload_json"]),
                    }
                    for r in rows
                ],
            )
        if p.path == "/artifacts":
            with conn() as c:
                rows = c.execute("SELECT * FROM artifacts ORDER BY created_at DESC LIMIT 200").fetchall()
            return self._json(
                200,
                [
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
                ],
            )
        if p.path == "/monitor/cameras":
            with conn() as c:
                rows = c.execute(
                    """
                    SELECT
                      c.id, c.name, c.status,
                      h.connected, h.last_connect_reason, h.ring_running, h.ring_restart_count, h.last_ring_exit_code, h.last_probe_at
                    FROM cameras c
                    LEFT JOIN recorder_camera_health h ON h.camera_id = c.id
                    ORDER BY c.created_at ASC
                    """
                ).fetchall()
            return self._json(
                200,
                [
                    {
                        "cameraId": r["id"],
                        "name": r["name"],
                        "status": r["status"],
                        "connected": None if r["connected"] is None else bool(r["connected"]),
                        "lastConnectReason": r["last_connect_reason"],
                        "ringRunning": None if r["ring_running"] is None else bool(r["ring_running"]),
                        "ringRestartCount": r["ring_restart_count"],
                        "lastRingExitCode": r["last_ring_exit_code"],
                        "lastProbeAt": r["last_probe_at"],
                    }
                    for r in rows
                ],
            )

        self._text(404, "not found")

    def do_POST(self):
        p = urlparse(self.path)
        b = self._read_json()
        if p.path.startswith("/cameras/") and p.path.endswith("/snapshot"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            with conn() as c:
                cam = c.execute(
                    "SELECT id, name, rtsp_url FROM cameras WHERE id = ?",
                    (cam_id,),
                ).fetchone()
            if not cam:
                return self._json(404, {"detail": "camera not found"})
            rtsp_url = self._normalize_rtsp_url(str(cam["rtsp_url"] or ""))
            try:
                frame = self._read_rtsp_frame(rtsp_url)
                import cv2  # type: ignore

                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not ok:
                    return self._json(500, {"detail": "jpeg_encode_failed"})
                b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
            except Exception as ex:
                return self._json(500, {"detail": f"snapshot failed: {ex}"})
            return self._json(
                200,
                {
                    "cameraId": str(cam["id"]),
                    "cameraName": str(cam["name"] or ""),
                    "capturedAt": now_iso(),
                    "mimeType": "image/jpeg",
                    "imageDataUrl": f"data:image/jpeg;base64,{b64}",
                },
            )
        if p.path == "/cameras/discover":
            cidr = str(b.get("cidr", "192.168.10.0/24"))
            username = str(b.get("username", ""))
            password = str(b.get("password", ""))
            timeout_sec = max(int(b.get("timeoutMs", 500)), 100) / 1000.0
            use_onvif = bool(b.get("useOnvif", True))
            onvif_timeout_ms = int(b.get("onvifTimeoutMs", 1500))
            try:
                ports = [int(x) for x in (b.get("ports") or [554, 8554]) if 1 <= int(x) <= 65535]
            except Exception:
                ports = [554]
            if not ports:
                ports = [554]
            host_candidates = []
            cidr_raw = cidr.strip().lower()
            if cidr_raw in ("", "auto", "all", "full", "auto-full"):
                full_scan = cidr_raw in ("", "auto", "all", "full", "auto-full")
                effective_cidrs = self._auto_cidr_candidates(full_scan=full_scan)
            else:
                effective_cidrs = [cidr.strip()]

            for cdr in effective_cidrs:
                try:
                    net = ipaddress.ip_network(cdr, strict=False)
                    host_candidates.extend([str(h) for h in net.hosts()])
                except Exception:
                    return self._json(400, {"detail": "invalid cidr"})
            onvif_devices = []
            if use_onvif:
                onvif_devices = self._onvif_discover(onvif_timeout_ms)
                for d in onvif_devices:
                    host_candidates.insert(0, d["ip"])
            seen_hosts = set()
            hosts = []
            for ip in host_candidates:
                if ip in seen_hosts:
                    continue
                seen_hosts.add(ip)
                hosts.append(ip)
            max_hosts_cap = 65536 if cidr_raw in ("", "auto", "all", "full", "auto-full") else 2048
            max_hosts = min(max(int(b.get("maxHosts", 4096)), 1), max_hosts_cap)
            hosts = hosts[:max_hosts]
            found = []
            scanned = 0
            onvif_map = {d["ip"]: d for d in onvif_devices}
            with ThreadPoolExecutor(max_workers=64) as ex:
                futs = [ex.submit(self._scan_host, ip, username, password, ports, timeout_sec) for ip in hosts]
                for f in as_completed(futs):
                    scanned += 1
                    r = f.result()
                    if r.get("found"):
                        if r["ip"] in onvif_map:
                            r["onvif"] = onvif_map[r["ip"]]
                        found.append(r)
            found.sort(key=lambda x: x["ip"])
            return self._json(
                200,
                {
                    "cidr": cidr,
                    "effectiveCidrs": effective_cidrs,
                    "onvifFound": len(onvif_devices),
                    "onvifDevices": onvif_devices,
                    "scannedHosts": scanned,
                    "foundCount": len(found),
                    "cameras": found,
                },
            )
        if p.path == "/cameras":
            cid = str(uuid.uuid4())
            with conn() as c:
                c.execute(
                    """
                    INSERT INTO cameras (id, name, rtsp_url, onvif_profile, webrtc_path, enabled, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'offline', ?, ?)
                    """,
                    (cid, b.get("name", ""), b.get("rtspUrl", ""), b.get("onvifProfile"), b.get("webrtcPath", ""), 1, now_iso(), now_iso()),
                )
                c.commit()
            return self._json(201, {"id": cid, "name": b.get("name"), "rtspUrl": b.get("rtspUrl"), "webrtcPath": b.get("webrtcPath"), "status": "offline", "enabled": True})
        if p.path == "/destinations":
            did = str(uuid.uuid4())
            with conn() as c:
                c.execute(
                    "INSERT INTO destinations (id, name, type, enabled, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (did, b.get("name", ""), b.get("type", "https_post"), 1, json.dumps(b.get("config", {})), now_iso(), now_iso()),
                )
                c.commit()
            return self._json(201, {"id": did, "name": b.get("name"), "type": b.get("type"), "enabled": True, "config": b.get("config", {})})
        if p.path == "/routing-rules":
            rid = str(uuid.uuid4())
            with conn() as c:
                c.execute(
                    "INSERT INTO routing_rules (id, camera_id, event_type, artifact_kind, destination_id, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, b.get("cameraId"), b.get("eventType", "motion"), b.get("artifactKind", "both"), b.get("destinationId"), 1, now_iso(), now_iso()),
                )
                c.commit()
            return self._json(201, {"id": rid, "cameraId": b.get("cameraId"), "eventType": b.get("eventType"), "artifactKind": b.get("artifactKind"), "destinationId": b.get("destinationId"), "enabled": True})
        if p.path == "/events":
            eid = str(uuid.uuid4())
            with conn() as c:
                c.execute(
                    "INSERT INTO events (id, camera_id, event_type, severity, occurred_at, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (eid, b.get("cameraId"), b.get("type", "motion"), b.get("severity", "medium"), now_iso(), json.dumps(b.get("payload", {})), now_iso()),
                )
                c.commit()
            return self._json(201, {"id": eid, "cameraId": b.get("cameraId"), "type": b.get("type", "motion"), "severity": b.get("severity", "medium"), "occurredAt": now_iso(), "payload": b.get("payload", {})})
        if p.path == "/auth/login":
            role = "admin"
            username = b.get("username", "dev")
            return self._json(200, {"accessToken": f"dev-token-{username}", "role": role})
        self._text(404, "not found")

    def do_PUT(self):
        p = urlparse(self.path)
        if p.path.startswith("/cameras/") and p.path.endswith("/roi"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            b = self._read_json()
            enabled = bool(b.get("enabled", False))
            zones = b.get("zones", [])
            if not isinstance(zones, list):
                zones = []
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
                    (cam_id, 1 if enabled else 0, json.dumps(zones), now_iso()),
                )
                c.commit()
            return self._json(200, {"cameraId": cam_id, "enabled": enabled, "zones": zones})
        if p.path == "/settings/ai-model":
            b = self._read_json()
            with conn() as c:
                c.execute(
                    "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('ai_model', ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (json.dumps(b), now_iso()),
                )
                c.commit()
            return self._json(200, b)
        if p.path == "/settings/person-event":
            b = self._read_json()
            cfg = {
                "enabled": bool(b.get("enabled", True)),
                "dwellSec": max(int(b.get("dwellSec", 5)), 1),
                "cooldownSec": max(int(b.get("cooldownSec", 10)), 0),
                "eventType": str(b.get("eventType", "person_detected") or "person_detected"),
                "severity": str(b.get("severity", "high") or "high"),
            }
            with conn() as c:
                c.execute(
                    "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('person_event_rule', ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (json.dumps(cfg), now_iso()),
                )
                c.commit()
            return self._json(200, cfg)
        if p.path.startswith("/cameras/") and p.path.endswith("/model-settings"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            b = self._read_json()
            cfg = {
                "enabled": bool(b.get("enabled", False)),
                "modelPath": str(b.get("modelPath", "") or ""),
                "confidenceThreshold": max(0.05, min(0.95, float(b.get("confidenceThreshold", 0.35)))),
                "timeoutSec": max(1, int(b.get("timeoutSec", 5))),
                "pollSec": max(0, float(b.get("pollSec", 2))),
                "cooldownSec": max(0, int(b.get("cooldownSec", 10))),
                "extra": b.get("extra", {}) if isinstance(b.get("extra", {}), dict) else {},
            }
            key = f"camera_model:{cam_id}"
            with conn() as c:
                c.execute(
                    "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, json.dumps(cfg), now_iso()),
                )
                c.commit()
            return self._json(200, cfg)
        self._text(404, "not found")

    def do_PATCH(self):
        p = urlparse(self.path)
        b = self._read_json()
        if p.path.startswith("/cameras/") and not p.path.endswith("/event-policy"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 2:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            with conn() as c:
                cur = c.execute("SELECT * FROM cameras WHERE id = ?", (cam_id,))
                row = cur.fetchone()
                if not row:
                    return self._json(404, {"detail": "camera not found"})
                name = str(b.get("name", row["name"]) or row["name"])
                rtsp_url = str(b.get("rtspUrl", row["rtsp_url"]) or row["rtsp_url"])
                webrtc_path = str(b.get("webrtcPath", row["webrtc_path"]) or row["webrtc_path"])
                enabled = 1 if bool(b.get("enabled", bool(row["enabled"]))) else 0
                c.execute(
                    """
                    UPDATE cameras
                    SET name = ?, rtsp_url = ?, webrtc_path = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, rtsp_url, webrtc_path, enabled, now_iso(), cam_id),
                )
                c.commit()
            return self._json(
                200,
                {
                    "id": cam_id,
                    "name": name,
                    "rtspUrl": rtsp_url,
                    "webrtcPath": webrtc_path,
                    "enabled": bool(enabled),
                },
            )
        if p.path.startswith("/cameras/") and p.path.endswith("/event-policy"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            clip = b.get("clip", {}) or {}
            snap = b.get("snapshot", {}) or {}
            with conn() as c:
                c.execute(
                    """
                    INSERT INTO event_policies (
                      id, camera_id, event_type, mode, clip_pre_sec, clip_post_sec, clip_cooldown_sec, clip_merge_window_sec,
                      snapshot_count, snapshot_interval_ms, snapshot_format, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(camera_id, event_type) DO UPDATE SET
                      mode=excluded.mode, clip_pre_sec=excluded.clip_pre_sec, clip_post_sec=excluded.clip_post_sec,
                      clip_cooldown_sec=excluded.clip_cooldown_sec, clip_merge_window_sec=excluded.clip_merge_window_sec,
                      snapshot_count=excluded.snapshot_count, snapshot_interval_ms=excluded.snapshot_interval_ms,
                      snapshot_format=excluded.snapshot_format, updated_at=excluded.updated_at
                    """,
                    (
                        str(uuid.uuid4()),
                        cam_id,
                        b.get("eventType", "motion"),
                        b.get("mode", "snapshot"),
                        int(clip.get("preSec", 10)),
                        int(clip.get("postSec", 20)),
                        int(clip.get("cooldownSec", 5)),
                        int(clip.get("mergeWindowSec", 3)),
                        int(snap.get("snapshotCount", 1)),
                        int(snap.get("intervalMs", 0)),
                        str(snap.get("format", "jpg")),
                        now_iso(),
                        now_iso(),
                    ),
                )
                c.commit()
            return self._json(200, {"cameraId": cam_id, "eventType": b.get("eventType", "motion"), "mode": b.get("mode", "snapshot"), "clip": clip, "snapshot": snap})
        self._text(404, "not found")

    def do_DELETE(self):
        p = urlparse(self.path)
        if p.path.startswith("/cameras/") and p.path.endswith("/event-policy"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 3:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            qq = parse_qs(p.query)
            event_type = (qq.get("eventType") or [""])[0].strip()
            if not event_type:
                return self._json(400, {"detail": "eventType is required"})
            with conn() as c:
                cur = c.execute(
                    "DELETE FROM event_policies WHERE camera_id = ? AND event_type = ?",
                    (cam_id, event_type),
                )
                c.commit()
                if cur.rowcount == 0:
                    return self._json(404, {"detail": "event policy not found"})
            self.send_response(204)
            self.end_headers()
            return
        if p.path.startswith("/cameras/"):
            parts = p.path.strip("/").split("/")
            if len(parts) != 2:
                return self._text(400, "invalid path")
            cam_id = parts[1]
            with conn() as c:
                c.execute("DELETE FROM event_policies WHERE camera_id = ?", (cam_id,))
                c.execute("DELETE FROM routing_rules WHERE camera_id = ?", (cam_id,))
                c.execute("DELETE FROM app_settings WHERE key IN (?, ?, ?)", (f"camera_model:{cam_id}", f"camera_roi:{cam_id}", f"camera_event_pack:{cam_id}"))
                cur = c.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))
                c.commit()
                if cur.rowcount == 0:
                    return self._json(404, {"detail": "camera not found"})
            self.send_response(204)
            self.end_headers()
            return
        self._text(404, "not found")


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
    print("SQLite dev server running on http://127.0.0.1:8080")
    server.serve_forever()

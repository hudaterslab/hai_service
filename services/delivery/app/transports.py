import os
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko
import requests

from .config import DeliverySettings
from .models import DeliveryJob, DeliveryResult


class DeliveryTransport:
    def send(self, job: DeliveryJob) -> DeliveryResult:
        raise NotImplementedError


class TransferNaming:
    def __init__(self, settings: DeliverySettings):
        self.settings = settings

    def parse_dt(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(raw)
            except Exception:
                pass
        return datetime.now(timezone.utc)

    def local_dt(self, value: Any) -> datetime:
        return self.parse_dt(value).astimezone(self.settings.system_tz)

    def local_text(self, value: Any) -> str:
        return self.local_dt(value).strftime("%Y%m%d_%H%M%S")

    def local_tz_name(self, value: Any) -> str:
        return self.safe_token(self.local_dt(value).tzname() or "LOCAL", "LOCAL")

    def safe_token(self, text: str, fallback: str) -> str:
        cleaned = re.sub(r"[^\w.-]+", "_", (text or "").strip(), flags=re.UNICODE).strip("._")
        return cleaned or fallback

    def event_label(self, job: DeliveryJob) -> str:
        return f"[{self.settings.device_name}][{job.camera_name}][{job.event_type}][{self.local_text(job.occurred_at)}{self.local_tz_name(job.occurred_at)}]"

    def safe_label(self, job: DeliveryJob) -> str:
        return (
            f"[{self.safe_token(self.settings.device_name, 'edge')}]"
            f"[{self.safe_token(job.camera_name, 'camera')}]"
            f"[{self.safe_token(job.event_type, 'event')}]"
            f"[{self.local_text(job.occurred_at)}{self.local_tz_name(job.occurred_at)}]"
        )


class HttpsDeliveryTransport(DeliveryTransport):
    EVENT_CODE_BY_TYPE = {
        "conveyor_crossing": 1,
        "person_cross_roi": 1,
        "helmet_missing": 2,
        "helmet_missing_in_roi": 2,
        "unauthorized_departure": 3,
        "vehicle_move_without_signalman": 3,
        "illegal_parking": 4,
        "no_parking_stop": 4,
    }

    def __init__(self, settings: DeliverySettings, naming: TransferNaming):
        self.settings = settings
        self.naming = naming

    def send(self, job: DeliveryJob) -> DeliveryResult:
        cfg = job.config
        api_mode = str(cfg.get("apiMode", "")).strip().lower()
        if api_mode != "cctv_img_v1":
            raise RuntimeError("unsupported apiMode for HTTPS destination; expected cctv_img_v1")
        if job.kind != "snapshot":
            raise RuntimeError("apiMode=cctv_img_v1 supports snapshot artifacts only")
        terminal_id = str(cfg.get("terminalId", "")).strip()
        if not terminal_id:
            raise RuntimeError("terminalId is required for apiMode=cctv_img_v1")
        cctv_id = self.resolve_cctv_id(cfg, job)
        collected_at = self.naming.local_dt(job.occurred_at).replace(tzinfo=None).isoformat(timespec="milliseconds")
        headers = self.auth_headers(cfg)
        files = {
            "image": (f"{self.naming.safe_label(job)}.jpg", open(job.local_path, "rb"), "image/jpeg"),
        }
        data = {
            "collectedAt": collected_at,
            "eventType": str(self.event_type_to_code(job.event_type)),
            "terminalId": terminal_id,
            "cctvId": str(cctv_id),
        }
        image_width = self._int_value(job.event_payload, "imageWidth", "frameWidth", "sourceWidth")
        image_height = self._int_value(job.event_payload, "imageHeight", "frameHeight", "sourceHeight")
        if image_width is not None:
            data["imageWidth"] = str(image_width)
        if image_height is not None:
            data["imageHeight"] = str(image_height)
        detected_objects = self._detected_objects(job, image_width, image_height)
        if detected_objects:
            data["detectedObjects"] = json.dumps(detected_objects, ensure_ascii=True)
        try:
            response = requests.post(cfg["url"], files=files, data=data, headers=headers, timeout=self.settings.timeout_sec)
            return DeliveryResult(
                ok=response.ok,
                status_code=response.status_code,
                error=None if response.ok else response.text[:1000],
            )
        finally:
            for _, item in files.items():
                try:
                    if isinstance(item, tuple) and len(item) >= 2 and hasattr(item[1], "close"):
                        item[1].close()
                except Exception:
                    pass

    def auth_headers(self, config: dict[str, Any]) -> dict[str, str]:
        auth = config.get("auth", {})
        if auth.get("type") != "bearer":
            return {}
        token = auth.get("token") or os.getenv(str(auth.get("token_env") or "").strip())
        return {"Authorization": f"Bearer {token}"} if token else {}

    def event_type_to_code(self, event_type: str) -> int:
        return int(self.EVENT_CODE_BY_TYPE.get(str(event_type or "").strip().lower(), 2))

    def resolve_cctv_id(self, cfg: dict[str, Any], job: DeliveryJob) -> int:
        by_camera_id = cfg.get("cctvIdByCameraId")
        if isinstance(by_camera_id, dict) and job.camera_id in by_camera_id:
            return int(by_camera_id[job.camera_id])
        by_camera_name = cfg.get("cctvIdByCameraName")
        if isinstance(by_camera_name, dict) and job.camera_name in by_camera_name:
            return int(by_camera_name[job.camera_name])
        if cfg.get("cctvId") is not None:
            return int(cfg.get("cctvId"))
        raise RuntimeError("cctvId is required for apiMode=cctv_img_v1")

    def _int_value(self, payload: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                parsed = int(round(float(value)))
            except Exception:
                continue
            if parsed > 0:
                return parsed
        return None

    def _detected_objects(self, job: DeliveryJob, image_width: int | None, image_height: int | None) -> list[dict[str, Any]]:
        raw = job.event_payload.get("detections")
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for det in raw:
            if not isinstance(det, dict):
                continue
            box = self._det_box_xyxy(det, image_width, image_height)
            if box is None:
                continue
            item = {
                "box": box,
                "label": str(det.get("label") or "object"),
                "score": round(float(det.get("confidence", det.get("score", 0.0)) or 0.0), 4),
            }
            result.append(item)
        return result

    def _det_box_xyxy(self, det: dict[str, Any], image_width: int | None, image_height: int | None) -> list[int] | None:
        direct = self._direct_box_xyxy(det)
        if direct is not None:
            return [max(0, int(round(v))) for v in direct]
        if image_width is None or image_height is None:
            return None
        if all(key in det for key in ("nx", "ny", "nw", "nh")):
            x1 = float(det.get("nx", 0.0)) * image_width
            y1 = float(det.get("ny", 0.0)) * image_height
            x2 = (float(det.get("nx", 0.0)) + float(det.get("nw", 0.0))) * image_width
            y2 = (float(det.get("ny", 0.0)) + float(det.get("nh", 0.0))) * image_height
            return self._clamp_box(x1, y1, x2, y2, image_width, image_height)
        if all(key in det for key in ("x", "y", "w", "h")):
            x1 = float(det.get("x", 0.0)) * image_width
            y1 = float(det.get("y", 0.0)) * image_height
            x2 = (float(det.get("x", 0.0)) + float(det.get("w", 0.0))) * image_width
            y2 = (float(det.get("y", 0.0)) + float(det.get("h", 0.0))) * image_height
            return self._clamp_box(x1, y1, x2, y2, image_width, image_height)
        return None

    def _direct_box_xyxy(self, det: dict[str, Any]) -> tuple[float, float, float, float] | None:
        if all(key in det for key in ("x1", "y1", "x2", "y2")):
            return (
                float(det.get("x1", 0.0)),
                float(det.get("y1", 0.0)),
                float(det.get("x2", 0.0)),
                float(det.get("y2", 0.0)),
            )
        box = det.get("box")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                return float(box[0]), float(box[1]), float(box[2]), float(box[3])
            except Exception:
                return None
        return None

    def _clamp_box(self, x1: float, y1: float, x2: float, y2: float, image_width: int, image_height: int) -> list[int]:
        left = min(max(int(round(x1)), 0), image_width)
        top = min(max(int(round(y1)), 0), image_height)
        right = min(max(int(round(x2)), 0), image_width)
        bottom = min(max(int(round(y2)), 0), image_height)
        return [left, top, max(left, right), max(top, bottom)]


class SftpDeliveryTransport(DeliveryTransport):
    def __init__(self, naming: TransferNaming):
        self.naming = naming

    def send(self, job: DeliveryJob) -> DeliveryResult:
        cfg = job.config
        host = cfg["host"]
        port = int(cfg.get("port", 22))
        username = cfg["username"]
        remote_path = cfg.get("remote_path", "/incoming")
        key_path = cfg.get("private_key_path")
        local = Path(job.local_path)
        label_safe = self.naming.safe_label(job)
        remote_file = f"{remote_path.rstrip('/')}/{label_safe}{local.suffix or ('.jpg' if job.kind == 'snapshot' else '.mp4')}"
        remote_log = f"{remote_path.rstrip('/')}/{label_safe}.log"
        transport = paramiko.Transport((host, port))
        if key_path:
            pkey = paramiko.RSAKey.from_private_key_file(key_path)
            transport.connect(username=username, pkey=pkey)
        else:
            transport.connect(username=username, password=cfg["password"])
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            sftp.put(str(local), remote_file)
            with sftp.open(remote_log, "w") as f:
                f.write(self.naming.event_label(job) + "\n")
        finally:
            sftp.close()
            transport.close()
        return DeliveryResult(ok=True, status_code=200, error=None)

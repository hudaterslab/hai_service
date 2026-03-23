import os
import re
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

    def kst_text(self, value: Any) -> str:
        return self.parse_dt(value).astimezone(self.settings.kst).strftime("%Y%m%d_%H%M%S")

    def safe_token(self, text: str, fallback: str) -> str:
        cleaned = re.sub(r"[^\w.-]+", "_", (text or "").strip(), flags=re.UNICODE).strip("._")
        return cleaned or fallback

    def event_label(self, job: DeliveryJob) -> str:
        return f"[{self.settings.device_name}][{job.camera_name}][{job.event_type}][{self.kst_text(job.occurred_at)}KST]"

    def safe_label(self, job: DeliveryJob) -> str:
        return (
            f"[{self.safe_token(self.settings.device_name, 'edge')}]"
            f"[{self.safe_token(job.camera_name, 'camera')}]"
            f"[{self.safe_token(job.event_type, 'event')}]"
            f"[{self.kst_text(job.occurred_at)}KST]"
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
        collected_at = self.naming.parse_dt(job.occurred_at).replace(tzinfo=None).isoformat(timespec="milliseconds")
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

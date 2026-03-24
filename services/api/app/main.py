import os
import json
import hmac
import hashlib
import secrets
import base64
import ipaddress
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4
from urllib.parse import quote
from urllib.parse import urlparse as urlparse_std
import xml.etree.ElementTree as ET

import jwt
import psycopg
import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from psycopg.rows import dict_row

try:
    from services.delivery.app.config import DeliverySettings
    from services.delivery.app.models import DeliveryJob
    from services.delivery.app.transports import HttpsDeliveryTransport, SftpDeliveryTransport, TransferNaming
except ModuleNotFoundError:
    from delivery_app.config import DeliverySettings
    from delivery_app.models import DeliveryJob
    from delivery_app.transports import HttpsDeliveryTransport, SftpDeliveryTransport, TransferNaming


DATABASE_URL = os.getenv("DATABASE_URL", "postgres://vms:vms@postgres:5432/vms?sslmode=disable")
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_BASE_DIR = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else Path("/app")
BASE_DIR = Path(os.getenv("PROJECT_ROOT", str(DEFAULT_BASE_DIR)))
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "runtime" / "media")))
EVENT_PACKS_DIR = BASE_DIR / "config" / "event_packs"
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
SNAPSHOT_TIMEOUT_SEC = max(float(os.getenv("SNAPSHOT_TIMEOUT_SEC", "8")), 2.0)
RTSP_FALLBACK_PATH = os.getenv("RTSP_FALLBACK_PATH", "/Streaming/Channels/101")
MODEL_PYTHON_BIN = os.getenv("MODEL_PYTHON_BIN", "python3")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
DEFAULT_USERS_JSON = '[{"username":"admin","password":"admin","role":"admin"},{"username":"operator","password":"operator","role":"operator"}]'
AUTH_USERS = json.loads(os.getenv("AUTH_USERS_JSON", DEFAULT_USERS_JSON))
DXNN_HOST_INFER_URL = os.getenv("DXNN_HOST_INFER_URL", "").strip()
MONITOR_HTTP_TIMEOUT_SEC = max(float(os.getenv("MONITOR_HTTP_TIMEOUT_SEC", "3.0")), 0.5)
MONITOR_RECORDER_STALE_SEC = max(int(os.getenv("MONITOR_RECORDER_STALE_SEC", "20")), 5)
KST = timezone(timedelta(hours=9), "KST")
SYSTEM_TZ = datetime.now().astimezone().tzinfo or timezone.utc
KST_TIME_ONLY_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<millis>\d{1,3}))?)?$")

app = FastAPI(title="Edge Console Control API", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
bearer = HTTPBearer(auto_error=False)
DISCOVER_JOBS: dict[str, dict[str, Any]] = {}
DISCOVER_JOBS_LOCK = threading.Lock()


def _env_path_list(name: str) -> list[Path]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]


def _default_model_roots() -> list[Path]:
    return [BASE_DIR / "models", BASE_DIR]


def _model_roots() -> list[Path]:
    roots = _env_path_list("MODEL_SEARCH_ROOTS") or _default_model_roots()
    uniq: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(root)
    return uniq


def _runner_candidates(env_name: str, bundled_name: str) -> list[Path]:
    candidates: list[Path] = []
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        candidates.append(Path(env_value).expanduser())
    for root in _model_roots():
        candidates.append(root / bundled_name)
    return candidates


def db_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


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
    type: str = Field(pattern="^https_post$")
    enabled: bool = True
    config: dict[str, Any]


class UpdateDestinationRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


DEST_TYPE_HTTPS_POST = "https_post"
DEST_API_MODE_CCTV_IMG_V1 = "cctv_img_v1"


def _as_positive_int(value: Any, field_name: str) -> int:
    try:
        n = int(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a positive integer")
    if n <= 0:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a positive integer")
    return n


def _normalize_cctv_id_map(value: Any) -> Optional[dict[str, int]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="config.cctvIdByCameraId must be an object")
    out: dict[str, int] = {}
    for k, v in value.items():
        key = str(k or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="config.cctvIdByCameraId key cannot be empty")
        out[key] = _as_positive_int(v, f"config.cctvIdByCameraId[{key}]")
    return out or None


def _normalize_destination_config(dest_type: str, config: Any) -> dict[str, Any]:
    if dest_type != DEST_TYPE_HTTPS_POST:
        raise HTTPException(status_code=400, detail="only https_post destination type is supported")
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    url = str(config.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="config.url is required")

    api_mode = str(config.get("apiMode") or "").strip().lower()
    if api_mode != DEST_API_MODE_CCTV_IMG_V1:
        raise HTTPException(status_code=400, detail="config.apiMode must be 'cctv_img_v1'")

    terminal_id = str(config.get("terminalId") or "").strip()
    if not terminal_id:
        raise HTTPException(status_code=400, detail="config.terminalId is required")

    cctv_id = None
    if config.get("cctvId") is not None and str(config.get("cctvId")).strip() != "":
        cctv_id = _as_positive_int(config.get("cctvId"), "config.cctvId")
    cctv_id_map = _normalize_cctv_id_map(config.get("cctvIdByCameraId"))
    if cctv_id is None and not cctv_id_map:
        raise HTTPException(status_code=400, detail="config.cctvId or config.cctvIdByCameraId is required")

    out: dict[str, Any] = {
        "url": url,
        "apiMode": DEST_API_MODE_CCTV_IMG_V1,
        "terminalId": terminal_id,
    }
    if cctv_id is not None:
        out["cctvId"] = cctv_id
    if cctv_id_map:
        out["cctvIdByCameraId"] = cctv_id_map

    auth = config.get("auth")
    if isinstance(auth, dict):
        token_env = str(auth.get("token_env") or "").strip()
        token = str(auth.get("token") or "").strip()
        if token_env:
            out["auth"] = {"type": "bearer", "token_env": token_env}
        elif token:
            out["auth"] = {"type": "bearer", "token": token}
    return out


def _to_iso8601(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(SYSTEM_TZ).isoformat(timespec="milliseconds")
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        return _to_iso8601(parsed)
    return None


def _now_iso8601() -> str:
    return _to_iso8601(datetime.now(timezone.utc)) or ""


def _normalize_kst_datetime_input(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return None
    matched = KST_TIME_ONLY_RE.fullmatch(raw)
    if matched:
        now_local = datetime.now(SYSTEM_TZ)
        second = int(matched.group("second") or "0")
        millis_raw = matched.group("millis") or "0"
        millis = int(millis_raw.ljust(3, "0")[:3])
        return now_local.replace(
            hour=int(matched.group("hour")),
            minute=int(matched.group("minute")),
            second=second,
            microsecond=millis * 1000,
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SYSTEM_TZ)
    return parsed


def _merge_infer_metadata(payload: dict[str, Any], infer: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload or {})
    infer_payload = infer.get("payload") if isinstance(infer.get("payload"), dict) else {}
    merged.update(infer_payload)
    raw_dets = infer.get("detections") if isinstance(infer.get("detections"), list) else []
    detections = [det for det in raw_dets if isinstance(det, dict)]
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
        value = infer_payload.get(src_key)
        if value is None:
            continue
        try:
            merged[dst_key] = int(round(float(value)))
        except Exception:
            continue
    return merged


def _probe_http(url: str, *, method: str = "GET", headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        resp = requests.request(method, url, headers=headers or {}, timeout=MONITOR_HTTP_TIMEOUT_SEC, allow_redirects=True)
        latency_ms = int((time.perf_counter() - started) * 1000)
        body_json = None
        content_type = str(resp.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                body_json = resp.json()
            except Exception:
                body_json = None
        return {
            "url": url,
            "reachable": True,
            "ok": resp.status_code < 500,
            "httpStatus": resp.status_code,
            "latencyMs": latency_ms,
            "body": body_json,
            "error": None,
        }
    except Exception as ex:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "url": url,
            "reachable": False,
            "ok": False,
            "httpStatus": None,
            "latencyMs": latency_ms,
            "body": None,
            "error": str(ex),
        }


def _dest_auth_headers(config: dict[str, Any]) -> dict[str, str]:
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return {}
    if str(auth.get("type") or "").strip().lower() != "bearer":
        return {}
    token = str(auth.get("token") or "").strip()
    token_env = str(auth.get("token_env") or "").strip()
    if not token and token_env:
        token = str(os.getenv(token_env) or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _dxnn_health_url() -> str:
    if not DXNN_HOST_INFER_URL:
        return ""
    try:
        parsed = urlparse_std(DXNN_HOST_INFER_URL)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return parsed._replace(path="/healthz", params="", query="", fragment="").geturl()
    except Exception:
        return ""


class CreateRoutingRuleRequest(BaseModel):
    cameraId: UUID
    eventType: str = "*"
    artifactKind: str = Field(default="both", pattern="^(clip|snapshot|both)$")
    destinationId: UUID
    enabled: bool = True


class UpdateRoutingRuleRequest(BaseModel):
    enabled: Optional[bool] = None


class CreateEventRequest(BaseModel):
    cameraId: UUID
    type: str = "motion"
    severity: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)


class CaptureStoredSnapshotRequest(BaseModel):
    eventType: str = "manual_snapshot"
    severity: str = "low"
    occurredAt: Optional[datetime] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurredAt", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: Any) -> Any:
        return _normalize_kst_datetime_input(value)


class CaptureVideoSnapshotRequest(CaptureStoredSnapshotRequest):
    videoPath: str
    offsetSec: float = 0.0


class VideoInferSendRequest(BaseModel):
    videoPath: str
    destinationId: UUID
    eventType: str = "helmet_missing_in_roi"
    severity: str = "high"
    startOffsetSec: float = 0.0
    endOffsetSec: Optional[float] = None
    sampleIntervalSec: float = 0.25
    cooldownSec: float = 5.0
    maxTriggers: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)


class ArtifactSendTestRequest(BaseModel):
    destinationId: UUID


class AIModelSettings(BaseModel):
    enabled: bool = False
    modelPath: str = ""
    timeoutSec: int = 5
    pollSec: int = 2
    cooldownSec: int = 10


class CameraROIRequest(BaseModel):
    enabled: bool = False
    zones: list[dict[str, Any]] = Field(default_factory=list)


class PersonEventRuleSettings(BaseModel):
    enabled: bool = True
    dwellSec: int = 5
    cooldownSec: int = 10
    eventType: str = "person_detected"
    severity: str = "high"


class WebRTCSettings(BaseModel):
    enabled: bool = True


class CameraModelSettings(BaseModel):
    enabled: bool = False
    modelPath: str = ""
    confidenceThreshold: float = 0.35
    timeoutSec: int = 5
    pollSec: int = 2
    cooldownSec: int = 10
    extra: dict[str, Any] = Field(default_factory=dict)


class CameraEventPackSettings(BaseModel):
    enabled: bool = False
    packId: str = "edge-basic"
    packVersion: str = "1.0.0"
    params: dict[str, Any] = Field(default_factory=dict)


class DiscoverCamerasRequest(BaseModel):
    cidr: str = "192.168.10.0/24"
    username: str = ""
    password: str = ""
    ports: list[int] = Field(default_factory=lambda: [554])
    maxHosts: int = 4096
    timeoutMs: int = 700
    useOnvif: bool = False
    onvifTimeoutMs: int = 1500


class LoginRequest(BaseModel):
    username: str
    password: str


class HashPasswordRequest(BaseModel):
    password: str
    iterations: int = 390000


def find_user(username: str) -> Optional[dict]:
    for u in AUTH_USERS:
        if u.get("username") == username:
            return u
    return None


def hash_password_pbkdf2(password: str, iterations: int = 390000) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, user: dict) -> bool:
    # Preferred: passwordHash field with PBKDF2 format.
    ph = user.get("passwordHash")
    if isinstance(ph, str) and ph.startswith("pbkdf2_sha256$"):
        try:
            _, iter_s, salt, digest = ph.split("$", 3)
            iterations = int(iter_s)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
            return hmac.compare_digest(dk, digest)
        except Exception:
            return False
    # Legacy fallback for compatibility.
    return user.get("password") == password


def issue_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=max(JWT_EXPIRE_MINUTES, 1))
    payload = {
        "sub": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    if not AUTH_ENABLED:
        return {"sub": "dev", "role": "admin"}
    if not credentials:
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")
    return {"sub": payload.get("sub"), "role": payload.get("role", "operator")}


def require_roles(*roles: str):
    def checker(user: dict = Depends(get_current_user)):
        if not AUTH_ENABLED:
            return user
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return checker


def _candidate_rtsp_urls(ip: str, username: str, password: str, ports: list[int]) -> list[str]:
    auth = ""
    if username:
        auth = f"{quote(username)}:{quote(password)}@"
    paths = [
        "/",
        "/Streaming/Channels/101",
        "/Streaming/Channels/102",
        "/Streaming/channels/101",
        "/Streaming/channels/102",
        "/Streaming/Channels/1",
        "/Streaming/channels/1",
        "/ISAPI/Streaming/channels/101",
        "/ISAPI/Streaming/channels/102",
        "/h264/ch1/main/av_stream",
        "/h264/ch1/sub/av_stream",
        "/h265/ch1/main/av_stream",
        "/live/ch00_0",
        "/live",
        "/h264",
        "/ch01/0",
        "/ch01/1",
        "/cam/realmonitor?channel=1&subtype=1",
        "/stream1",
        "/cam/realmonitor?channel=1&subtype=0",
    ]
    urls: list[str] = []
    seen: set[str] = set()
    for p in ports:
        for path in paths:
            u = f"rtsp://{auth}{ip}:{p}{path}"
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def _probe_rtsp(url: str, timeout_sec: float) -> tuple[bool, str]:
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


def _scan_host(ip: str, username: str, password: str, ports: list[int], timeout_sec: float) -> dict[str, Any]:
    for url in _candidate_rtsp_urls(ip, username, password, ports):
        ok, detail = _probe_rtsp(url, timeout_sec)
        if ok:
            return {"ip": ip, "found": True, "rtspUrl": url, "detail": detail}
    return {"ip": ip, "found": False, "rtspUrl": None, "detail": "no_rtsp_response"}


def _onvif_probe_xml() -> str:
    msg_id = f"uuid:{uuid4()}"
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


def _onvif_discover(timeout_ms: int) -> list[dict[str, Any]]:
    payload = _onvif_probe_xml().encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(max(timeout_ms, 300) / 1000.0)
    try:
        # Sending a couple of probes improves hit rate on some devices.
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
    devices: dict[str, dict[str, Any]] = {}
    while datetime.now(timezone.utc).timestamp() < end_ts:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            break
        except Exception:
            continue
        ip = addr[0]
        xaddrs: list[str] = []
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


def _auto_cidr_candidates(full_scan: bool = False) -> list[str]:
    # Best-effort: local /24 + common private ranges.
    out: list[str] = []
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
        # Wide scan over common private /24 ranges.
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


def _camera_public(row: dict[str, Any]) -> dict[str, Any]:
    extra = row.get("model_extra_json")
    if not isinstance(extra, dict):
        extra = {}
    rotate_raw = row.get("rotation_deg", extra.get("rotationDeg", 0))
    try:
        rotate_deg = int(rotate_raw)
    except Exception:
        rotate_deg = 0
    if rotate_deg not in (90, 180, 270):
        rotate_deg = 0
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "rtspUrl": row["rtsp_url"],
        "onvifProfile": row["onvif_profile"],
        "webrtcPath": row["webrtc_path"],
        "enabled": row["enabled"],
        "status": row["status"],
        "rotationDeg": rotate_deg,
    }


def _list_model_candidates() -> list[dict[str, Any]]:
    exts = {".pt", ".onnx", ".engine", ".py", ".exe", ".dxnn"}
    roots = _model_roots()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in exts:
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
                    "source": "models" if (BASE_DIR / "models") in p.parents else "project",
                }
            )
    out.sort(key=lambda x: (x["source"], x["name"].lower(), x["path"].lower()))
    return out


def _event_pack_files() -> list[Path]:
    if not EVENT_PACKS_DIR.exists():
        return []
    return sorted(EVENT_PACKS_DIR.glob("*.json"))


def _load_event_pack_from_path(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    pack_id = str(raw.get("packId", "")).strip()
    version = str(raw.get("version", "")).strip()
    events = raw.get("events", [])
    if not pack_id or not version or not isinstance(events, list):
        return {}
    return {
        "packId": pack_id,
        "version": version,
        "name": str(raw.get("name", f"{pack_id}@{version}")),
        "description": str(raw.get("description", "")),
        "events": events,
        "sourceFile": str(path),
    }


def _load_event_packs() -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for p in _event_pack_files():
        pack = _load_event_pack_from_path(p)
        if pack:
            packs.append(pack)
    return packs


def _find_event_pack(pack_id: str, version: Optional[str]) -> Optional[dict[str, Any]]:
    pack_id = pack_id.strip()
    target_version = (version or "").strip()
    matches = [p for p in _load_event_packs() if p.get("packId") == pack_id]
    if not matches:
        return None
    if target_version:
        for m in matches:
            if m.get("version") == target_version:
                return m
        return None
    return sorted(matches, key=lambda x: str(x.get("version", "")), reverse=True)[0]


def _normalize_rotate_deg(value: Any) -> int:
    try:
        n = int(value)
    except Exception:
        return 0
    return n if n in (90, 180, 270) else 0


def _rotation_filter_for_ffmpeg(rotate_deg: int) -> Optional[str]:
    deg = _normalize_rotate_deg(rotate_deg)
    if deg == 90:
        return "transpose=1"
    if deg == 180:
        return "hflip,vflip"
    if deg == 270:
        return "transpose=2"
    return None


def _capture_snapshot_bytes(rtsp_url: str, rotate_deg: int = 0) -> bytes:
    rtsp_url = _normalize_rtsp_url(rtsp_url)
    tmp_dir = MEDIA_ROOT / "snapshots"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"api_snapshot_{uuid4().hex}.jpg"
    errors: list[str] = []
    vf = _rotation_filter_for_ffmpeg(rotate_deg)
    for transport in ("tcp", "udp"):
        cmd = [
            FFMPEG_BIN,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-rtsp_transport",
            transport,
            "-i",
            rtsp_url,
        ]
        if vf:
            cmd.extend(["-vf", vf])
        cmd.extend(["-frames:v", "1", str(tmp_path)])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SNAPSHOT_TIMEOUT_SEC + 2, check=False)
        if proc.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
            try:
                return tmp_path.read_bytes()
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        err = proc.stderr.strip().splitlines()
        tail = (err[-1] if err else "snapshot_capture_failed")[:200]
        errors.append(f"{transport}:{tail}")
    raise RuntimeError("; ".join(errors[:2]) or "snapshot_capture_failed")


def _capture_snapshot_bytes_from_video_file(video_path: str, offset_sec: float = 0.0, rotate_deg: int = 0) -> bytes:
    source_path = Path(str(video_path or "").strip()).expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"video file not found: {source_path}")
    tmp_dir = MEDIA_ROOT / "snapshots"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"api_video_snapshot_{uuid4().hex}.jpg"
    vf = _rotation_filter_for_ffmpeg(rotate_deg)
    cmd = [
        FFMPEG_BIN,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(float(offset_sec), 0.0):.3f}",
        "-i",
        str(source_path),
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend(["-frames:v", "1", str(tmp_path)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SNAPSHOT_TIMEOUT_SEC + 5, check=False)
    if proc.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
        try:
            return tmp_path.read_bytes()
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
    err = proc.stderr.strip().splitlines()
    tail = (err[-1] if err else "video_snapshot_capture_failed")[:200]
    raise RuntimeError(tail)


def _probe_media_duration_sec(video_path: str) -> float:
    source_path = Path(str(video_path or "").strip()).expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"video file not found: {source_path}")
    proc = subprocess.run(
        [
            FFPROBE_BIN,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("video_duration_probe_failed")
    raw = (proc.stdout or "").strip()
    try:
        duration = float(raw)
    except Exception as ex:
        raise RuntimeError(f"video_duration_parse_failed:{ex}")
    if duration <= 0:
        raise RuntimeError("video_duration_invalid")
    return duration


def _load_camera_inference_context(camera_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, bool, float, dict[str, Any], int]:
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              c.id, c.name, c.rtsp_url, c.status, c.webrtc_path,
              cms.enabled AS model_enabled,
              cms.model_path,
              cms.timeout_sec,
              cms.confidence_threshold,
              cms.extra_json AS model_extra_json,
              cr.enabled AS roi_enabled,
              cr.zones_json
            FROM cameras c
            LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
            LEFT JOIN camera_rois cr ON cr.camera_id = c.id
            WHERE c.id = %s
            """,
            (camera_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="camera not found")

        cur.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'")
        global_model = cur.fetchone()
        cur.execute("SELECT value_json FROM app_settings WHERE key = 'person_event_rule'")
        person_rule_row = cur.fetchone()

    roi = {
        "enabled": bool(row.get("roi_enabled", False)),
        "zones": row.get("zones_json") if isinstance(row.get("zones_json"), list) else [],
    }
    extra = row.get("model_extra_json") if isinstance(row.get("model_extra_json"), dict) else {}
    rotate_deg = _normalize_rotate_deg(extra.get("rotationDeg", 0))

    selected_global_model = ""
    global_timeout_sec = 5
    if global_model and isinstance(global_model.get("value_json"), dict):
        gv = global_model.get("value_json") or {}
        selected_global_model = str(gv.get("modelPath", "") or "")
        try:
            global_timeout_sec = max(int(gv.get("timeoutSec", 5)), 1)
        except Exception:
            global_timeout_sec = 5
    model_path = str(row.get("model_path") or selected_global_model or "").strip()
    model_enabled = bool(row.get("model_enabled", False))
    camera_conf = float(row.get("confidence_threshold") or 0.35)

    person_rule = {
        "enabled": True,
        "dwellSec": 5,
        "cooldownSec": 10,
        "eventType": "person_detected",
        "severity": "high",
    }
    if person_rule_row and isinstance(person_rule_row.get("value_json"), dict):
        src = person_rule_row.get("value_json") or {}
        person_rule["enabled"] = bool(src.get("enabled", True))
        person_rule["dwellSec"] = max(int(src.get("dwellSec", 5)), 1)
        person_rule["cooldownSec"] = max(int(src.get("cooldownSec", 10)), 0)
        person_rule["eventType"] = str(src.get("eventType", "person_detected") or "person_detected")
        person_rule["severity"] = str(src.get("severity", "high") or "high")

    return row, roi, person_rule, model_path, model_enabled, camera_conf, extra, global_timeout_sec


def _run_model_inference(
    row: dict[str, Any],
    roi: dict[str, Any],
    person_rule: dict[str, Any],
    model_path: str,
    model_enabled: bool,
    conf_thres: float,
    extra: dict[str, Any],
    rotate_deg: int,
    global_timeout_sec: int,
    *,
    event_type: str,
    severity: str,
    rtsp_url: str = "",
    video_path: str = "",
    offset_sec: float = 0.0,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "trigger": False,
        "label": "",
        "payload": {},
        "detections": [],
        "count": 0,
        "status": "disabled",
    }
    if not model_enabled or not model_path:
        out["status"] = "model_disabled_or_empty_path"
        return out

    path = Path(model_path)
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
            out["status"] = "runner_not_found"
            return out
        cmd = [MODEL_PYTHON_BIN, str(runner_path)]
        if model_ext == ".dxnn":
            run_env["DXNN_MODEL_PATH"] = model_path
        else:
            run_env["YOLO_MODEL_PATH"] = model_path
            run_env.setdefault("YOLO_CONFIG_DIR", str(MEDIA_ROOT / ".ultralytics"))
            Path(run_env["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

    try:
        timeout_sec = max(int(row.get("timeout_sec") or global_timeout_sec), 1)
    except Exception:
        timeout_sec = global_timeout_sec
    req = {
        "cameraId": str(row["id"]),
        "cameraName": str(row["name"] or ""),
        "eventType": event_type,
        "severity": severity,
        "rtspUrl": rtsp_url,
        "videoPath": video_path,
        "offsetSec": max(float(offset_sec), 0.0),
        "webrtcPath": str(row.get("webrtc_path") or ""),
        "timestamp": _now_iso8601(),
        "roi": roi,
        "personEventRule": person_rule,
        "confidenceThreshold": conf_thres,
        "modelPath": model_path,
        "extra": extra,
        "rotationDeg": rotate_deg,
    }
    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(req),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=run_env,
        )
    except Exception as ex:
        out["status"] = f"model_exec_error:{str(ex)[:80]}"
        out["payload"] = {"reason": str(ex)}
        return out

    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0:
        out["status"] = f"model_exit_{proc.returncode}"
        out["payload"] = {"stderr": (proc.stderr or "").strip()[:400]}
        return out
    if not stdout:
        out["status"] = "model_empty_stdout"
        return out
    try:
        raw = json.loads(stdout)
    except Exception:
        out["status"] = "model_bad_json"
        out["payload"] = {"stdout": stdout[:400]}
        return out

    raw_dets = raw.get("detections", [])
    detections = [d for d in raw_dets if isinstance(d, dict)] if isinstance(raw_dets, list) else []
    payload = raw.get("payload", {}) if isinstance(raw.get("payload"), dict) else {}
    payload_count = payload.get("personCount")
    if isinstance(payload_count, (int, float)):
        count = int(max(0, payload_count))
    elif detections:
        count = len(detections)
    else:
        count = 1 if bool(raw.get("trigger", False)) else 0

    out.update(
        {
            "trigger": bool(raw.get("trigger", False)),
            "label": str(raw.get("label", "") or ""),
            "payload": payload,
            "detections": detections,
            "count": count,
            "status": "ok" if str(raw.get("label", "")) != "model-error" else f"model-error:{str(payload.get('reason', 'unknown'))[:48]}",
            "eventType": str(raw.get("eventType", event_type) or event_type),
            "severity": str(raw.get("severity", severity) or severity),
        }
    )
    return out


def _safe_artifact_token(text: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in (text or "").strip()).strip("._")
    return cleaned or fallback


def _manual_snapshot_path(camera_id: str, camera_name: str, event_type: str, occurred_at: datetime) -> Path:
    label = "_".join(
        [
            occurred_at.astimezone(SYSTEM_TZ).strftime("%Y%m%dT%H%M%S"),
            _safe_artifact_token(camera_name, "camera"),
            _safe_artifact_token(event_type, "event"),
            uuid4().hex[:8],
        ]
    )
    directory = MEDIA_ROOT / "manual-snapshots" / camera_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{label}.jpg"


def _store_snapshot_artifact(
    *,
    camera_id: str,
    camera_name: str,
    image_bytes: bytes,
    event_type: str,
    severity: str,
    occurred_at: Optional[datetime],
    payload: dict[str, Any],
) -> dict[str, Any]:
    occurred_dt = occurred_at.astimezone(timezone.utc) if occurred_at else datetime.now(timezone.utc)
    local_path = _manual_snapshot_path(camera_id, camera_name, event_type, occurred_dt)
    local_path.write_bytes(image_bytes)
    checksum_sha256 = hashlib.sha256(image_bytes).hexdigest()
    size_bytes = len(image_bytes)

    event_payload = dict(payload or {})
    event_payload.setdefault("source", "manual_cli_snapshot")
    event_payload.setdefault("storedPath", str(local_path))

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (camera_id, event_type, severity, occurred_at, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (camera_id, event_type, severity, occurred_dt, json.dumps(event_payload)),
        )
        event_row = cur.fetchone()
        cur.execute(
            """
            INSERT INTO artifacts (event_id, camera_id, kind, local_path, uri, mime_type, checksum_sha256, size_bytes)
            VALUES (%s, %s, 'snapshot', %s, %s, 'image/jpeg', %s, %s)
            RETURNING *
            """,
            (str(event_row["id"]), camera_id, str(local_path), None, checksum_sha256, size_bytes),
        )
        artifact_row = cur.fetchone()
        conn.commit()

    return {
        "artifactId": str(artifact_row["id"]),
        "eventId": str(event_row["id"]),
        "cameraId": camera_id,
        "cameraName": camera_name,
        "eventType": event_type,
        "severity": severity,
        "occurredAt": _to_iso8601(occurred_dt),
        "localPath": str(local_path),
        "mimeType": "image/jpeg",
        "checksumSha256": checksum_sha256,
        "sizeBytes": size_bytes,
        "createdAt": _to_iso8601(artifact_row["created_at"]),
    }


def _send_artifact_to_destination_now(artifact_id: str, destination_id: str) -> dict[str, Any]:
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id AS artifact_id,
              a.camera_id,
              a.kind,
              a.local_path,
              a.checksum_sha256,
              e.id AS event_id,
              e.event_type,
              e.occurred_at,
              c.name AS camera_name,
              d.id AS destination_id,
              d.name AS destination_name,
              d.type AS destination_type,
              d.enabled AS destination_enabled,
              d.config_json
            FROM artifacts a
            JOIN events e ON e.id = a.event_id
            JOIN cameras c ON c.id = a.camera_id
            JOIN destinations d ON d.id = %s
            WHERE a.id = %s
            """,
            (destination_id, artifact_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="artifact or destination not found")

        local_path = Path(str(row["local_path"] or ""))
        if not local_path.exists():
            raise HTTPException(status_code=404, detail="artifact file not found on disk")

        settings = DeliverySettings()
        naming = TransferNaming(settings)
        transports = {
            "https_post": HttpsDeliveryTransport(settings, naming),
            "sftp": SftpDeliveryTransport(naming),
        }
        transport = transports.get(str(row["destination_type"] or ""))
        if transport is None:
            raise HTTPException(status_code=400, detail="unsupported destination type")

        job = DeliveryJob(
            id=f"manual-test-{uuid4()}",
            artifact_id=str(row["artifact_id"]),
            destination_id=str(row["destination_id"]),
            destination_type=str(row["destination_type"]),
            destination_enabled=bool(row["destination_enabled"]),
            config=row["config_json"] if isinstance(row["config_json"], dict) else {},
            event_type=str(row["event_type"] or ""),
            occurred_at=row["occurred_at"],
            camera_id=str(row["camera_id"]),
            camera_name=str(row["camera_name"] or ""),
            kind=str(row["kind"] or ""),
            local_path=str(local_path),
            checksum_sha256=str(row["checksum_sha256"] or ""),
            attempt_no=1,
        )

        result = transport.send(job)
        cur.execute(
            """
            INSERT INTO delivery_attempts (artifact_id, destination_id, status, attempt_no, http_status, error_message, next_retry_at)
            VALUES (%s, %s, %s, 1, %s, %s, CASE WHEN %s = 'failed' THEN NOW() ELSE NULL END)
            RETURNING id, created_at, updated_at
            """,
            (
                artifact_id,
                destination_id,
                "success" if result.ok else "failed",
                result.status_code,
                result.error,
                "success" if result.ok else "failed",
            ),
        )
        attempt_row = cur.fetchone()
        if result.ok:
            cur.execute(
                "UPDATE artifacts SET uri = COALESCE(uri, %s) WHERE id = %s",
                (f"manual-test:{destination_id}", artifact_id),
            )
        conn.commit()

    return {
        "ok": result.ok,
        "statusCode": result.status_code,
        "error": result.error,
        "artifactId": artifact_id,
        "destinationId": destination_id,
        "attemptId": str(attempt_row["id"]),
        "attemptedAt": _to_iso8601(attempt_row["updated_at"] or attempt_row["created_at"]),
    }


def _normalize_rtsp_url(rtsp_url: str) -> str:
    raw = str(rtsp_url or "").strip()
    if not raw:
        return raw

    # Some cameras use passwords containing reserved characters like '#', '?', '@'.
    # If the URL was entered without encoding, stdlib parsing can fail to extract host.
    # In that case, try a best-effort userinfo normalization first.
    if raw.lower().startswith("rtsp://"):
        try:
            parsed_raw = urlparse_std(raw)
            if not parsed_raw.hostname and "@" in raw[7:]:
                tail = raw[7:]
                at = tail.rfind("@")
                userinfo = tail[:at]
                hostpath = tail[at + 1 :]
                if ":" in userinfo:
                    u, p = userinfo.split(":", 1)
                    userinfo_norm = f"{quote(u, safe='')}:{quote(p, safe='')}"
                else:
                    userinfo_norm = quote(userinfo, safe="")
                raw = f"rtsp://{userinfo_norm}@{hostpath}"
        except Exception:
            pass

    try:
        parsed = urlparse_std(raw)
        if parsed.scheme.lower() != "rtsp" or not parsed.hostname:
            return raw
        if parsed.path and parsed.path not in ("", "/"):
            return raw
        auth = ""
        if parsed.username:
            auth = quote(parsed.username, safe="")
            if parsed.password is not None:
                auth += f":{quote(parsed.password, safe='')}"
            auth += "@"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port or 554
        query = f"?{parsed.query}" if parsed.query else ""
        return f"rtsp://{auth}{host}:{port}{RTSP_FALLBACK_PATH}{query}"
    except Exception:
        return raw


def _run_discovery(
    *,
    cidr: str,
    username: str,
    password: str,
    ports: list[int],
    max_hosts_req: int,
    timeout_ms: int,
    use_onvif: bool,
    onvif_timeout_ms: int,
    progress_cb=None,
) -> dict[str, Any]:
    timeout_sec = max(timeout_ms, 100) / 1000.0
    ports = [int(p) for p in ports if 1 <= int(p) <= 65535]
    if not ports:
        ports = [554]

    host_candidates: list[str] = []
    cidr_raw = cidr.strip().lower()
    effective_cidrs: list[str] = []
    if cidr_raw in ("", "auto", "all", "full", "auto-full"):
        effective_cidrs = _auto_cidr_candidates(full_scan=True)
    else:
        effective_cidrs = [cidr.strip()]

    for one_cidr in effective_cidrs:
        net = ipaddress.ip_network(one_cidr, strict=False)
        for h in net.hosts():
            host_candidates.append(str(h))

    onvif_devices: list[dict[str, Any]] = []
    if use_onvif:
        onvif_devices = _onvif_discover(onvif_timeout_ms)
        for d in onvif_devices:
            host_candidates.insert(0, d["ip"])

    seen_hosts: set[str] = set()
    hosts: list[str] = []
    for ip in host_candidates:
        if ip in seen_hosts:
            continue
        seen_hosts.add(ip)
        hosts.append(ip)
    max_hosts_cap = 65536 if cidr_raw in ("", "auto", "all", "full", "auto-full") else 4096
    max_hosts = min(max(int(max_hosts_req), 1), max_hosts_cap)
    hosts = hosts[:max_hosts]

    found: list[dict[str, Any]] = []
    scanned = 0
    total_hosts = len(hosts)
    onvif_map = {d["ip"]: d for d in onvif_devices}
    if progress_cb:
        progress_cb(scanned, total_hosts, len(found), "scan_started")
    with ThreadPoolExecutor(max_workers=64) as ex:
        futs = [ex.submit(_scan_host, ip, username, password, ports, timeout_sec) for ip in hosts]
        for f in as_completed(futs):
            scanned += 1
            r = f.result()
            if r.get("found"):
                if r["ip"] in onvif_map:
                    r["onvif"] = onvif_map[r["ip"]]
                found.append(r)
            if progress_cb and (scanned % 8 == 0 or scanned == total_hosts):
                progress_cb(scanned, total_hosts, len(found), "scanning")
    found.sort(key=lambda x: x["ip"])
    if progress_cb:
        progress_cb(scanned, total_hosts, len(found), "completed")
    return {
        "cidr": cidr,
        "effectiveCidrs": effective_cidrs,
        "onvifFound": len(onvif_devices),
        "onvifDevices": onvif_devices,
        "scannedHosts": scanned,
        "foundCount": len(found),
        "cameras": found,
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/auth/login")
def login(body: LoginRequest):
    if not AUTH_ENABLED:
        return {"accessToken": issue_token("dev", "admin"), "role": "admin"}
    u = find_user(body.username)
    if not u or not verify_password(body.password, u):
        raise HTTPException(status_code=401, detail="invalid credentials")
    role = u.get("role", "operator")
    return {"accessToken": issue_token(body.username, role), "role": role}


@app.post("/auth/hash-password")
def hash_password(body: HashPasswordRequest, _=Depends(require_roles("admin"))):
    return {"passwordHash": hash_password_pbkdf2(body.password, body.iterations)}


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"username": user.get("sub"), "role": user.get("role"), "authEnabled": AUTH_ENABLED}


@app.get("/")
def ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/cameras")
def list_cameras(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM (
              SELECT DISTINCT ON (c.id)
                c.*,
                cms.extra_json AS model_extra_json
              FROM cameras c
              LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
              ORDER BY c.id, cms.updated_at DESC NULLS LAST
            ) t
            ORDER BY t.created_at DESC
            """
        )
        rows = cur.fetchall()
    return [_camera_public(r) for r in rows]


@app.post("/cameras/discover")
def discover_cameras(body: DiscoverCamerasRequest, _=Depends(require_roles("admin", "operator"))):
    try:
        return _run_discovery(
            cidr=body.cidr,
            username=body.username,
            password=body.password,
            ports=body.ports,
            max_hosts_req=body.maxHosts,
            timeout_ms=body.timeoutMs,
            use_onvif=body.useOnvif,
            onvif_timeout_ms=body.onvifTimeoutMs,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="invalid discover request")


@app.post("/cameras/discover/jobs", status_code=202)
def start_discover_job(body: DiscoverCamerasRequest, _=Depends(require_roles("admin", "operator"))):
    job_id = str(uuid4())
    now = _now_iso8601()
    job = {
        "jobId": job_id,
        "status": "queued",
        "message": "queued",
        "createdAt": now,
        "updatedAt": now,
        "progress": {"scannedHosts": 0, "totalHosts": 0, "foundCount": 0},
        "result": None,
        "error": None,
    }
    with DISCOVER_JOBS_LOCK:
        DISCOVER_JOBS[job_id] = job

    def _worker():
        try:
            with DISCOVER_JOBS_LOCK:
                DISCOVER_JOBS[job_id]["status"] = "running"
                DISCOVER_JOBS[job_id]["message"] = "running"
                DISCOVER_JOBS[job_id]["updatedAt"] = _now_iso8601()

            def _progress(scanned: int, total: int, found: int, phase: str):
                with DISCOVER_JOBS_LOCK:
                    if job_id not in DISCOVER_JOBS:
                        return
                    DISCOVER_JOBS[job_id]["progress"] = {
                        "scannedHosts": scanned,
                        "totalHosts": total,
                        "foundCount": found,
                    }
                    DISCOVER_JOBS[job_id]["message"] = phase
                    DISCOVER_JOBS[job_id]["updatedAt"] = _now_iso8601()

            result = _run_discovery(
                cidr=body.cidr,
                username=body.username,
                password=body.password,
                ports=body.ports,
                max_hosts_req=body.maxHosts,
                timeout_ms=body.timeoutMs,
                use_onvif=body.useOnvif,
                onvif_timeout_ms=body.onvifTimeoutMs,
                progress_cb=_progress,
            )
            with DISCOVER_JOBS_LOCK:
                DISCOVER_JOBS[job_id]["status"] = "done"
                DISCOVER_JOBS[job_id]["message"] = "done"
                DISCOVER_JOBS[job_id]["result"] = result
                DISCOVER_JOBS[job_id]["updatedAt"] = _now_iso8601()
        except Exception as ex:
            with DISCOVER_JOBS_LOCK:
                DISCOVER_JOBS[job_id]["status"] = "error"
                DISCOVER_JOBS[job_id]["message"] = "error"
                DISCOVER_JOBS[job_id]["error"] = str(ex)
                DISCOVER_JOBS[job_id]["updatedAt"] = _now_iso8601()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"jobId": job_id, "status": "queued"}


@app.get("/cameras/discover/jobs/{job_id}")
def get_discover_job(job_id: str, _=Depends(require_roles("admin", "operator"))):
    with DISCOVER_JOBS_LOCK:
        job = DISCOVER_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job


@app.post("/cameras", status_code=201)
def create_camera(body: CreateCameraRequest, _=Depends(require_roles("admin", "operator"))):
    rtsp_url = _normalize_rtsp_url(body.rtspUrl)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cameras (name, rtsp_url, onvif_profile, webrtc_path, enabled)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (body.name, rtsp_url, body.onvifProfile, body.webrtcPath, body.enabled),
        )
        row = cur.fetchone()
        conn.commit()
    return _camera_public(row)


@app.patch("/cameras/{camera_id}")
def patch_camera(camera_id: UUID, body: UpdateCameraRequest, _=Depends(require_roles("admin"))):
    updates: list[str] = []
    values: list[Any] = []
    mapping = {
        "name": body.name,
        "rtsp_url": body.rtspUrl,
        "webrtc_path": body.webrtcPath,
        "onvif_profile": body.onvifProfile,
        "enabled": body.enabled,
    }
    for column, value in mapping.items():
        if value is not None:
            if column == "rtsp_url":
                value = _normalize_rtsp_url(str(value))
            updates.append(f"{column} = %s")
            values.append(value)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    values.extend([str(camera_id)])
    query = f"UPDATE cameras SET {', '.join(updates)}, updated_at = NOW() WHERE id = %s RETURNING *"
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="camera not found")
    return _camera_public(row)


@app.delete("/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: UUID, _=Depends(require_roles("admin"))):
    cid = str(camera_id)
    with db_conn() as conn, conn.cursor() as cur:
        # Clean dependent rows first to avoid FK constraint failures on camera delete.
        cur.execute("DELETE FROM event_policies WHERE camera_id = %s", (cid,))
        cur.execute("DELETE FROM routing_rules WHERE camera_id = %s", (cid,))
        cur.execute("DELETE FROM app_settings WHERE key IN (%s, %s, %s)", (f"camera_model:{cid}", f"camera_roi:{cid}", f"camera_event_pack:{cid}"))
        cur.execute("DELETE FROM cameras WHERE id = %s", (cid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="camera not found")
        conn.commit()
    return None


@app.patch("/cameras/{camera_id}/event-policy")
def upsert_event_policy(camera_id: UUID, body: UpsertEventPolicyRequest, _=Depends(require_roles("admin", "operator"))):
    clip = body.clip or ClipConfig()
    snapshot = body.snapshot or SnapshotConfig()
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_policies (
              camera_id, event_type, mode,
              clip_pre_sec, clip_post_sec, clip_cooldown_sec, clip_merge_window_sec,
              snapshot_count, snapshot_interval_ms, snapshot_format
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (camera_id, event_type) DO UPDATE SET
              mode = EXCLUDED.mode,
              clip_pre_sec = EXCLUDED.clip_pre_sec,
              clip_post_sec = EXCLUDED.clip_post_sec,
              clip_cooldown_sec = EXCLUDED.clip_cooldown_sec,
              clip_merge_window_sec = EXCLUDED.clip_merge_window_sec,
              snapshot_count = EXCLUDED.snapshot_count,
              snapshot_interval_ms = EXCLUDED.snapshot_interval_ms,
              snapshot_format = EXCLUDED.snapshot_format,
              updated_at = NOW()
            RETURNING *
            """,
            (
                str(camera_id),
                body.eventType,
                body.mode,
                clip.preSec,
                clip.postSec,
                clip.cooldownSec,
                clip.mergeWindowSec,
                snapshot.snapshotCount,
                snapshot.intervalMs,
                snapshot.format,
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "cameraId": str(row["camera_id"]),
        "eventType": row["event_type"],
        "mode": row["mode"],
        "clip": {
            "preSec": row["clip_pre_sec"],
            "postSec": row["clip_post_sec"],
            "cooldownSec": row["clip_cooldown_sec"],
            "mergeWindowSec": row["clip_merge_window_sec"],
        },
        "snapshot": {
            "snapshotCount": row["snapshot_count"],
            "intervalMs": row["snapshot_interval_ms"],
            "format": row["snapshot_format"],
        },
    }


@app.delete("/cameras/{camera_id}/event-policy", status_code=204)
def delete_event_policy(camera_id: UUID, eventType: str, _=Depends(require_roles("admin", "operator"))):
    ev = (eventType or "").strip()
    if not ev:
        raise HTTPException(status_code=400, detail="eventType is required")
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM event_policies WHERE camera_id = %s AND event_type = %s",
            (str(camera_id), ev),
        )
        conn.commit()
    return None


@app.get("/cameras/{camera_id}/roi")
def get_camera_roi(camera_id: UUID, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM camera_rois WHERE camera_id = %s", (str(camera_id),))
        row = cur.fetchone()
    if not row:
        return {"cameraId": str(camera_id), "enabled": False, "zones": []}
    return {
        "cameraId": str(row["camera_id"]),
        "enabled": bool(row["enabled"]),
        "zones": row["zones_json"] or [],
    }


@app.put("/cameras/{camera_id}/roi")
def put_camera_roi(camera_id: UUID, body: CameraROIRequest, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO camera_rois (camera_id, enabled, zones_json, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (camera_id) DO UPDATE
              SET enabled = EXCLUDED.enabled,
                  zones_json = EXCLUDED.zones_json,
                  updated_at = NOW()
            RETURNING *
            """,
            (str(camera_id), body.enabled, json.dumps(body.zones or [])),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "cameraId": str(row["camera_id"]),
        "enabled": bool(row["enabled"]),
        "zones": row["zones_json"] or [],
    }


@app.post("/cameras/{camera_id}/snapshot")
def capture_camera_snapshot(camera_id: UUID, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.rtsp_url, cms.extra_json AS model_extra_json
            FROM cameras c
            LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
            WHERE c.id = %s
            """,
            (str(camera_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="camera not found")
    extra = row.get("model_extra_json")
    rotate_deg = _normalize_rotate_deg(extra.get("rotationDeg", 0) if isinstance(extra, dict) else 0)
    try:
        image_bytes = _capture_snapshot_bytes(str(row["rtsp_url"]), rotate_deg=rotate_deg)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"snapshot failed: {ex}")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "cameraId": str(row["id"]),
        "cameraName": row["name"],
        "capturedAt": _now_iso8601(),
        "mimeType": "image/jpeg",
        "imageDataUrl": f"data:image/jpeg;base64,{b64}",
    }


@app.post("/cameras/{camera_id}/snapshot-artifact", status_code=201)
def capture_camera_snapshot_artifact(
    camera_id: UUID,
    body: CaptureStoredSnapshotRequest,
    _=Depends(require_roles("admin", "operator")),
):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.rtsp_url, cms.extra_json AS model_extra_json
            FROM cameras c
            LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
            WHERE c.id = %s
            """,
            (str(camera_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="camera not found")
    extra = row.get("model_extra_json")
    rotate_deg = _normalize_rotate_deg(extra.get("rotationDeg", 0) if isinstance(extra, dict) else 0)
    try:
        image_bytes = _capture_snapshot_bytes(str(row["rtsp_url"]), rotate_deg=rotate_deg)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"snapshot failed: {ex}")
    return _store_snapshot_artifact(
        camera_id=str(row["id"]),
        camera_name=str(row["name"] or ""),
        image_bytes=image_bytes,
        event_type=str(body.eventType or "manual_snapshot"),
        severity=str(body.severity or "low"),
        occurred_at=body.occurredAt,
        payload=body.payload,
    )


@app.post("/cameras/{camera_id}/video-snapshot-artifact", status_code=201)
def capture_camera_video_snapshot_artifact(
    camera_id: UUID,
    body: CaptureVideoSnapshotRequest,
    _=Depends(require_roles("admin", "operator")),
):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, cms.extra_json AS model_extra_json
            FROM cameras c
            LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
            WHERE c.id = %s
            """,
            (str(camera_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="camera not found")
    extra = row.get("model_extra_json")
    rotate_deg = _normalize_rotate_deg(extra.get("rotationDeg", 0) if isinstance(extra, dict) else 0)
    try:
        image_bytes = _capture_snapshot_bytes_from_video_file(body.videoPath, offset_sec=body.offsetSec, rotate_deg=rotate_deg)
    except FileNotFoundError as ex:
        raise HTTPException(status_code=404, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"video snapshot failed: {ex}")
    payload = dict(body.payload or {})
    payload.setdefault("source", "manual_cli_video")
    payload.setdefault("videoPath", body.videoPath)
    payload.setdefault("offsetSec", body.offsetSec)
    return _store_snapshot_artifact(
        camera_id=str(row["id"]),
        camera_name=str(row["name"] or ""),
        image_bytes=image_bytes,
        event_type=str(body.eventType or "manual_snapshot"),
        severity=str(body.severity or "low"),
        occurred_at=body.occurredAt,
        payload=payload,
    )


@app.post("/cameras/{camera_id}/video-infer-send")
def infer_and_send_video_events(
    camera_id: UUID,
    body: VideoInferSendRequest,
    _=Depends(require_roles("admin", "operator")),
):
    row, roi, person_rule, model_path, model_enabled, camera_conf, extra, global_timeout_sec = _load_camera_inference_context(str(camera_id))
    if not model_enabled or not model_path:
        raise HTTPException(status_code=400, detail="camera model is not enabled or modelPath is empty")

    start_offset = max(float(body.startOffsetSec or 0.0), 0.0)
    sample_interval = max(float(body.sampleIntervalSec or 0.25), 0.05)
    cooldown_sec = max(float(body.cooldownSec or 5.0), 0.0)
    max_triggers = max(int(body.maxTriggers or 1), 1)
    try:
        duration_sec = _probe_media_duration_sec(body.videoPath)
    except FileNotFoundError as ex:
        raise HTTPException(status_code=404, detail=str(ex))
    except Exception as ex:
        if body.endOffsetSec is None:
            raise HTTPException(status_code=400, detail=f"video duration probe failed: {ex}")
        duration_sec = max(float(body.endOffsetSec), start_offset)
    end_offset = min(max(float(body.endOffsetSec), start_offset), duration_sec) if body.endOffsetSec is not None else duration_sec
    conf_thres = max(0.05, min(camera_conf, 0.95))
    rotate_deg = _normalize_rotate_deg(extra.get("rotationDeg", 0))

    samples: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    cooldown_until = -1.0
    offset = start_offset
    while offset <= end_offset + 1e-9:
        sample_info: dict[str, Any] = {"offsetSec": round(offset, 3)}
        if offset < cooldown_until:
            sample_info["status"] = "cooldown_skip"
            samples.append(sample_info)
            offset += sample_interval
            continue

        infer = _run_model_inference(
            row,
            roi,
            person_rule,
            model_path,
            model_enabled,
            conf_thres,
            extra,
            rotate_deg,
            global_timeout_sec,
            event_type=str(body.eventType or "helmet_missing_in_roi"),
            severity=str(body.severity or "high"),
            video_path=body.videoPath,
            offset_sec=offset,
        )
        sample_info.update(
            {
                "status": infer.get("status"),
                "trigger": bool(infer.get("trigger", False)),
                "label": infer.get("label"),
                "eventType": infer.get("eventType"),
            }
        )
        samples.append(sample_info)

        if bool(infer.get("trigger", False)):
            try:
                image_bytes = _capture_snapshot_bytes_from_video_file(body.videoPath, offset_sec=offset, rotate_deg=rotate_deg)
            except Exception as ex:
                raise HTTPException(status_code=500, detail=f"video snapshot failed after trigger: {ex}")
            payload = _merge_infer_metadata(dict(body.payload or {}), infer)
            payload.setdefault("source", "video_infer_loop")
            payload.setdefault("videoPath", body.videoPath)
            payload["offsetSec"] = round(offset, 3)
            artifact = _store_snapshot_artifact(
                camera_id=str(row["id"]),
                camera_name=str(row["name"] or ""),
                image_bytes=image_bytes,
                event_type=str(infer.get("eventType") or body.eventType or "helmet_missing_in_roi"),
                severity=str(infer.get("severity") or body.severity or "high"),
                occurred_at=datetime.now(timezone.utc),
                payload=payload,
            )
            delivery = _send_artifact_to_destination_now(artifact["artifactId"], str(body.destinationId))
            deliveries.append({"offsetSec": round(offset, 3), "artifact": artifact, "delivery": delivery})
            cooldown_until = offset + cooldown_sec
            if len(deliveries) >= max_triggers:
                break
        offset += sample_interval

    return {
        "cameraId": str(row["id"]),
        "cameraName": str(row["name"] or ""),
        "videoPath": body.videoPath,
        "eventType": str(body.eventType or "helmet_missing_in_roi"),
        "sampleIntervalSec": sample_interval,
        "cooldownSec": cooldown_sec,
        "startOffsetSec": round(start_offset, 3),
        "endOffsetSec": round(end_offset, 3),
        "sampleCount": len(samples),
        "triggerCount": len(deliveries),
        "samples": samples,
        "deliveries": deliveries,
    }


@app.get("/cameras/{camera_id}/model-settings")
def get_camera_model_settings(camera_id: UUID, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM camera_model_settings WHERE camera_id = %s", (str(camera_id),))
        row = cur.fetchone()
    if not row:
        return CameraModelSettings().model_dump()
    return CameraModelSettings(
        enabled=bool(row["enabled"]),
        modelPath=str(row["model_path"] or ""),
        confidenceThreshold=float(row["confidence_threshold"] or 0.35),
        timeoutSec=int(row["timeout_sec"] or 5),
        pollSec=int(row["poll_sec"] or 2),
        cooldownSec=int(row["cooldown_sec"] or 10),
        extra=row["extra_json"] or {},
    ).model_dump()


@app.put("/cameras/{camera_id}/model-settings")
def put_camera_model_settings(camera_id: UUID, body: CameraModelSettings, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO camera_model_settings (
              camera_id, enabled, model_path, confidence_threshold, timeout_sec, poll_sec, cooldown_sec, extra_json, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (camera_id) DO UPDATE SET
              enabled = EXCLUDED.enabled,
              model_path = EXCLUDED.model_path,
              confidence_threshold = EXCLUDED.confidence_threshold,
              timeout_sec = EXCLUDED.timeout_sec,
              poll_sec = EXCLUDED.poll_sec,
              cooldown_sec = EXCLUDED.cooldown_sec,
              extra_json = EXCLUDED.extra_json,
              updated_at = NOW()
            """,
            (
                str(camera_id),
                body.enabled,
                body.modelPath,
                body.confidenceThreshold,
                body.timeoutSec,
                body.pollSec,
                body.cooldownSec,
                json.dumps(body.extra or {}),
            ),
        )
        conn.commit()
    return body.model_dump()


@app.get("/cameras/{camera_id}/event-pack")
def get_camera_event_pack(camera_id: UUID, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM camera_event_pack_settings WHERE camera_id = %s", (str(camera_id),))
        row = cur.fetchone()
    if not row:
        return CameraEventPackSettings().model_dump()
    return CameraEventPackSettings(
        enabled=bool(row["enabled"]),
        packId=str(row["pack_id"] or "edge-basic"),
        packVersion=str(row["pack_version"] or "1.0.0"),
        params=row["params_json"] or {},
    ).model_dump()


@app.put("/cameras/{camera_id}/event-pack")
def put_camera_event_pack(camera_id: UUID, body: CameraEventPackSettings, _=Depends(require_roles("admin", "operator"))):
    if not _find_event_pack(body.packId, body.packVersion):
        raise HTTPException(status_code=400, detail="event pack not found")
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO camera_event_pack_settings (camera_id, enabled, pack_id, pack_version, params_json, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (camera_id) DO UPDATE SET
              enabled = EXCLUDED.enabled,
              pack_id = EXCLUDED.pack_id,
              pack_version = EXCLUDED.pack_version,
              params_json = EXCLUDED.params_json,
              updated_at = NOW()
            """,
            (str(camera_id), body.enabled, body.packId, body.packVersion, json.dumps(body.params or {})),
        )
        conn.commit()
    return body.model_dump()


@app.get("/event-policies")
def list_event_policies(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM event_policies ORDER BY updated_at DESC")
        rows = cur.fetchall()
    return [
        {
            "cameraId": str(r["camera_id"]),
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
def get_ai_model_settings(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'")
        row = cur.fetchone()
    if not row:
        return AIModelSettings().model_dump()
    raw = row["value_json"] or {}
    return AIModelSettings(
        enabled=bool(raw.get("enabled", False)),
        modelPath=str(raw.get("modelPath", "")),
        timeoutSec=int(raw.get("timeoutSec", 5)),
        pollSec=int(raw.get("pollSec", 2)),
        cooldownSec=int(raw.get("cooldownSec", 10)),
    ).model_dump()


@app.put("/settings/ai-model")
def put_ai_model_settings(body: AIModelSettings, _=Depends(require_roles("admin"))):
    value = body.model_dump()
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES ('ai_model', %s, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value_json = EXCLUDED.value_json,
                  updated_at = NOW()
            """,
            (json.dumps(value),),
        )
        conn.commit()
    return value


@app.get("/settings/person-event")
def get_person_event_settings(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value_json FROM app_settings WHERE key = 'person_event_rule'")
        row = cur.fetchone()
    if not row:
        return PersonEventRuleSettings().model_dump()
    raw = row["value_json"] or {}
    return PersonEventRuleSettings(
        enabled=bool(raw.get("enabled", True)),
        dwellSec=max(int(raw.get("dwellSec", 5)), 1),
        cooldownSec=max(int(raw.get("cooldownSec", 10)), 0),
        eventType=str(raw.get("eventType", "person_detected")),
        severity=str(raw.get("severity", "high")),
    ).model_dump()


@app.put("/settings/person-event")
def put_person_event_settings(body: PersonEventRuleSettings, _=Depends(require_roles("admin", "operator"))):
    value = {
        "enabled": body.enabled,
        "dwellSec": max(int(body.dwellSec), 1),
        "cooldownSec": max(int(body.cooldownSec), 0),
        "eventType": body.eventType or "person_detected",
        "severity": body.severity or "high",
    }
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES ('person_event_rule', %s, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value_json = EXCLUDED.value_json,
                  updated_at = NOW()
            """,
            (json.dumps(value),),
        )
        conn.commit()
    return value


@app.get("/settings/webrtc")
def get_webrtc_settings(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value_json FROM app_settings WHERE key = 'webrtc'")
        row = cur.fetchone()
    if not row:
        return WebRTCSettings().model_dump()
    raw = row["value_json"] or {}
    return WebRTCSettings(enabled=bool(raw.get("enabled", True))).model_dump()


@app.put("/settings/webrtc")
def put_webrtc_settings(body: WebRTCSettings, _=Depends(require_roles("admin"))):
    value = {"enabled": bool(body.enabled)}
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES ('webrtc', %s, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value_json = EXCLUDED.value_json,
                  updated_at = NOW()
            """,
            (json.dumps(value),),
        )
        conn.commit()
    return value


@app.get("/models/list")
def list_models(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'")
        row = cur.fetchone()
    selected = ""
    if row and isinstance(row.get("value_json"), dict):
        selected = str((row["value_json"] or {}).get("modelPath", "") or "")
    return {"selectedPath": selected, "items": _list_model_candidates()}


@app.get("/dev/ai/preview")
def get_ai_preview(cameraId: str, conf: Optional[float] = None, _=Depends(require_roles("admin", "operator"))):
    camera_id = (cameraId or "").strip()
    if not camera_id:
        raise HTTPException(status_code=400, detail="cameraId is required")
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              c.id, c.name, c.rtsp_url, c.status, c.webrtc_path,
              cms.enabled AS model_enabled,
              cms.model_path,
              cms.timeout_sec,
              cms.confidence_threshold,
              cms.extra_json AS model_extra_json,
              cr.enabled AS roi_enabled,
              cr.zones_json
            FROM cameras c
            LEFT JOIN camera_model_settings cms ON cms.camera_id = c.id
            LEFT JOIN camera_rois cr ON cr.camera_id = c.id
            WHERE c.id = %s
            """,
            (camera_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="camera not found")

        cur.execute("SELECT value_json FROM app_settings WHERE key = 'ai_model'")
        global_model = cur.fetchone()
        cur.execute("SELECT value_json FROM app_settings WHERE key = 'person_event_rule'")
        person_rule_row = cur.fetchone()

    roi = {
        "enabled": bool(row.get("roi_enabled", False)),
        "zones": row.get("zones_json") if isinstance(row.get("zones_json"), list) else [],
    }
    extra = row.get("model_extra_json") if isinstance(row.get("model_extra_json"), dict) else {}
    rotate_deg = _normalize_rotate_deg(extra.get("rotationDeg", 0))
    rtsp_url = _normalize_rtsp_url(str(row["rtsp_url"] or ""))
    if not rtsp_url:
        raise HTTPException(status_code=400, detail="camera rtspUrl is empty")

    try:
        image_bytes = _capture_snapshot_bytes(rtsp_url, rotate_deg=rotate_deg)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"snapshot failed: {ex}")
    image_data_url = f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}"

    selected_global_model = ""
    global_timeout_sec = 5
    if global_model and isinstance(global_model.get("value_json"), dict):
        gv = global_model.get("value_json") or {}
        selected_global_model = str(gv.get("modelPath", "") or "")
        try:
            global_timeout_sec = max(int(gv.get("timeoutSec", 5)), 1)
        except Exception:
            global_timeout_sec = 5
    model_path = str(row.get("model_path") or selected_global_model or "").strip()
    model_enabled = bool(row.get("model_enabled", False))
    camera_conf = float(row.get("confidence_threshold") or 0.35)
    if conf is None:
        conf_thres = max(0.05, min(camera_conf, 0.95))
    else:
        conf_thres = max(0.05, min(float(conf), 0.95))

    person_rule = {
        "enabled": True,
        "dwellSec": 5,
        "cooldownSec": 10,
        "eventType": "person_detected",
        "severity": "high",
    }
    if person_rule_row and isinstance(person_rule_row.get("value_json"), dict):
        src = person_rule_row.get("value_json") or {}
        person_rule["enabled"] = bool(src.get("enabled", True))
        person_rule["dwellSec"] = max(int(src.get("dwellSec", 5)), 1)
        person_rule["cooldownSec"] = max(int(src.get("cooldownSec", 10)), 0)
        person_rule["eventType"] = str(src.get("eventType", "person_detected") or "person_detected")
        person_rule["severity"] = str(src.get("severity", "high") or "high")

    detections: list[dict[str, Any]] = []
    count = 0
    trigger = False
    label = ""
    payload: dict[str, Any] = {}
    inference_status = "disabled"

    if model_enabled and model_path:
        path = Path(model_path)
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
                cmd = []
                inference_status = "runner_not_found"
            else:
                cmd = [MODEL_PYTHON_BIN, str(runner_path)]
                if model_ext == ".dxnn":
                    run_env["DXNN_MODEL_PATH"] = model_path
                else:
                    run_env["YOLO_MODEL_PATH"] = model_path
                    run_env.setdefault("YOLO_CONFIG_DIR", str(MEDIA_ROOT / ".ultralytics"))
                    Path(run_env["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

        if cmd:
            try:
                timeout_sec = max(int(row.get("timeout_sec") or global_timeout_sec), 1)
            except Exception:
                timeout_sec = global_timeout_sec
            req = {
                "cameraId": str(row["id"]),
                "cameraName": str(row["name"] or ""),
                "eventType": "motion",
                "rtspUrl": rtsp_url,
                "webrtcPath": str(row.get("webrtc_path") or ""),
                "timestamp": _now_iso8601(),
                "roi": roi,
                "personEventRule": person_rule,
                "confidenceThreshold": conf_thres,
                "modelPath": model_path,
                "extra": extra,
                "rotationDeg": rotate_deg,
            }
            try:
                proc = subprocess.run(
                    cmd,
                    input=json.dumps(req),
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    check=False,
                    env=run_env,
                )
                stdout = (proc.stdout or "").strip()
                if proc.returncode != 0:
                    inference_status = f"model_exit_{proc.returncode}"
                elif not stdout:
                    inference_status = "model_empty_stdout"
                else:
                    try:
                        out = json.loads(stdout)
                        raw_dets = out.get("detections", [])
                        if isinstance(raw_dets, list):
                            detections = [d for d in raw_dets if isinstance(d, dict)]
                        trigger = bool(out.get("trigger", False))
                        label = str(out.get("label", "") or "")
                        payload = out.get("payload", {}) if isinstance(out.get("payload"), dict) else {}
                        payload_count = payload.get("personCount")
                        if isinstance(payload_count, (int, float)):
                            count = int(max(0, payload_count))
                        elif detections:
                            count = len(detections)
                        else:
                            count = 1 if trigger else 0
                        if label == "model-error":
                            reason = str(payload.get("reason", "unknown"))
                            inference_status = f"model-error:{reason[:48]}"
                        else:
                            inference_status = "ok"
                    except Exception:
                        inference_status = "model_bad_json"
            except Exception as ex:
                inference_status = f"model_exec_error:{str(ex)[:80]}"
    else:
        inference_status = "model_disabled_or_empty_path"

    return {
        "cameraId": str(row["id"]),
        "cameraName": row["name"],
        "status": f"{row['status']} [{inference_status}]",
        "webrtcPath": row["webrtc_path"],
        "rtspUrl": rtsp_url,
        "capturedAt": _now_iso8601(),
        "modelPath": model_path,
        "confThreshold": conf_thres,
        "trigger": trigger,
        "label": label,
        "payload": payload,
        "count": count,
        "roi": roi,
        "detections": detections,
        "imageDataUrl": image_data_url,
    }


@app.get("/event-packs")
def list_event_packs(_=Depends(require_roles("admin", "operator"))):
    packs = _load_event_packs()
    return [
        {
            "packId": p["packId"],
            "version": p["version"],
            "name": p["name"],
            "description": p["description"],
            "eventCount": len(p.get("events", [])),
        }
        for p in packs
    ]


@app.get("/event-packs/{pack_id}")
def get_event_pack(pack_id: str, version: Optional[str] = None, _=Depends(require_roles("admin", "operator"))):
    pack = _find_event_pack(pack_id, version)
    if not pack:
        raise HTTPException(status_code=404, detail="event pack not found")
    return pack


@app.get("/destinations")
def list_destinations(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM destinations ORDER BY created_at DESC")
        rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "type": r["type"],
            "enabled": r["enabled"],
            "config": r["config_json"],
        }
        for r in rows
    ]


@app.post("/destinations", status_code=201)
def create_destination(body: CreateDestinationRequest, _=Depends(require_roles("admin"))):
    normalized_config = _normalize_destination_config(body.type, body.config)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO destinations (name, type, enabled, config_json)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (body.name, body.type, body.enabled, json.dumps(normalized_config)),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "type": row["type"],
        "enabled": row["enabled"],
        "config": row["config_json"],
    }


@app.patch("/destinations/{destination_id}")
def patch_destination(destination_id: UUID, body: UpdateDestinationRequest, _=Depends(require_roles("admin"))):
    normalized_config = None
    if body.config is not None:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT type FROM destinations WHERE id = %s", (str(destination_id),))
            existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="destination not found")
        normalized_config = _normalize_destination_config(str(existing["type"] or ""), body.config)

    updates: list[str] = []
    values: list[Any] = []
    mapping = {
        "name": body.name,
        "enabled": body.enabled,
        "config_json": (json.dumps(normalized_config) if body.config is not None else None),
    }
    for column, value in mapping.items():
        if value is not None:
            updates.append(f"{column} = %s")
            values.append(value)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    values.extend([str(destination_id)])
    query = f"UPDATE destinations SET {', '.join(updates)}, updated_at = NOW() WHERE id = %s RETURNING *"
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="destination not found")
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "type": row["type"],
        "enabled": row["enabled"],
        "config": row["config_json"],
    }


@app.delete("/destinations/{destination_id}", status_code=204)
def delete_destination(destination_id: UUID, _=Depends(require_roles("admin"))):
    did = str(destination_id)
    with db_conn() as conn, conn.cursor() as cur:
        # Remove dependent rows first to avoid FK failures.
        cur.execute("DELETE FROM delivery_attempts WHERE destination_id = %s", (did,))
        cur.execute("DELETE FROM routing_rules WHERE destination_id = %s", (did,))
        cur.execute("DELETE FROM destinations WHERE id = %s", (did,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="destination not found")
        conn.commit()
    return None


@app.get("/routing-rules")
def list_routing_rules(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM routing_rules ORDER BY created_at DESC")
        rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "cameraId": str(r["camera_id"]),
            "eventType": r["event_type"],
            "artifactKind": r["artifact_kind"],
            "destinationId": str(r["destination_id"]),
            "enabled": r["enabled"],
        }
        for r in rows
    ]


@app.post("/routing-rules", status_code=201)
def create_routing_rule(body: CreateRoutingRuleRequest, _=Depends(require_roles("admin", "operator"))):
    event_type = (body.eventType or "").strip() or "*"
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO routing_rules (camera_id, event_type, artifact_kind, destination_id, enabled)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(body.cameraId), event_type, body.artifactKind, str(body.destinationId), body.enabled),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row["id"]),
        "cameraId": str(row["camera_id"]),
        "eventType": row["event_type"],
        "artifactKind": row["artifact_kind"],
        "destinationId": str(row["destination_id"]),
        "enabled": row["enabled"],
    }


@app.patch("/routing-rules/{rule_id}")
def patch_routing_rule(rule_id: UUID, body: UpdateRoutingRuleRequest, _=Depends(require_roles("admin", "operator"))):
    updates: list[str] = []
    values: list[Any] = []
    if body.enabled is not None:
        updates.append("enabled = %s")
        values.append(body.enabled)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    values.append(str(rule_id))
    query = f"UPDATE routing_rules SET {', '.join(updates)} WHERE id = %s RETURNING *"
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="routing rule not found")
    return {
        "id": str(row["id"]),
        "cameraId": str(row["camera_id"]),
        "eventType": row["event_type"],
        "artifactKind": row["artifact_kind"],
        "destinationId": str(row["destination_id"]),
        "enabled": row["enabled"],
    }


@app.delete("/routing-rules/{rule_id}", status_code=204)
def delete_routing_rule(rule_id: UUID, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM routing_rules WHERE id = %s", (str(rule_id),))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="routing rule not found")
        conn.commit()
    return None


@app.get("/events")
def list_events(cameraId: Optional[UUID] = None, type: Optional[str] = None, _=Depends(require_roles("admin", "operator"))):
    clauses = []
    values: list[Any] = []
    if cameraId:
        clauses.append("e.camera_id = %s")
        values.append(str(cameraId))
    if type:
        clauses.append("e.event_type = %s")
        values.append(type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT e.* FROM events e {where} ORDER BY e.occurred_at DESC LIMIT 200", values)
        rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "cameraId": str(r["camera_id"]),
            "type": r["event_type"],
            "severity": r["severity"],
            "occurredAt": _to_iso8601(r["occurred_at"]),
            "payload": r["payload_json"],
        }
        for r in rows
    ]


@app.get("/monitor/cameras")
def monitor_cameras(_=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              c.id,
              c.name,
              c.status,
              h.connected,
              h.last_connect_reason,
              h.ring_running,
              h.ring_restart_count,
              h.last_ring_exit_code,
              h.last_probe_at
            FROM cameras c
            LEFT JOIN recorder_camera_health h ON h.camera_id = c.id
            ORDER BY c.created_at ASC
            """
        )
        rows = cur.fetchall()
    return [
        {
            "cameraId": str(r["id"]),
            "name": r["name"],
            "status": r["status"],
            "connected": r["connected"],
            "lastConnectReason": r["last_connect_reason"],
            "ringRunning": r["ring_running"],
            "ringRestartCount": r["ring_restart_count"],
            "lastRingExitCode": r["last_ring_exit_code"],
            "lastProbeAt": _to_iso8601(r["last_probe_at"]),
        }
        for r in rows
    ]


@app.get("/monitor/overview")
def monitor_overview(_=Depends(require_roles("admin", "operator"))):
    now = datetime.now(timezone.utc)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        _ = cur.fetchone()
        cur.execute(
            """
            SELECT
              c.id,
              c.name,
              c.status,
              h.connected,
              h.last_connect_reason,
              h.ring_running,
              h.ring_restart_count,
              h.last_ring_exit_code,
              h.last_probe_at
            FROM cameras c
            LEFT JOIN recorder_camera_health h ON h.camera_id = c.id
            ORDER BY c.created_at ASC
            """
        )
        camera_rows = cur.fetchall()
        cur.execute(
            """
            SELECT
              d.id,
              d.name,
              d.type,
              d.enabled,
              d.config_json,
              last_attempt.status AS last_delivery_status,
              last_attempt.updated_at AS last_delivery_at,
              last_attempt.http_status AS last_delivery_http_status,
              last_attempt.error_message AS last_delivery_error
            FROM destinations d
            LEFT JOIN (
              SELECT DISTINCT ON (da.destination_id)
                da.destination_id,
                da.status,
                da.updated_at,
                da.http_status,
                da.error_message
              FROM delivery_attempts da
              ORDER BY da.destination_id, da.updated_at DESC
            ) last_attempt ON last_attempt.destination_id = d.id
            ORDER BY d.created_at ASC
            """
        )
        destination_rows = cur.fetchall()
        cur.execute(
            """
            SELECT
              rr.camera_id,
              rr.destination_id,
              rr.event_type,
              rr.artifact_kind,
              d.name AS destination_name
            FROM routing_rules rr
            JOIN destinations d ON d.id = rr.destination_id
            WHERE rr.enabled = TRUE
            ORDER BY rr.created_at ASC
            """
        )
        route_rows = cur.fetchall()

    dxnn_health_url = _dxnn_health_url()
    dxnn_probe = (
        _probe_http(dxnn_health_url, method="GET")
        if dxnn_health_url
        else {"url": None, "reachable": False, "ok": False, "httpStatus": None, "latencyMs": None, "body": None, "error": "DXNN_HOST_INFER_URL not configured"}
    )

    destination_states: dict[str, dict[str, Any]] = {}
    destinations_payload: list[dict[str, Any]] = []
    for row in destination_rows:
        cfg = row["config_json"] if isinstance(row["config_json"], dict) else {}
        url = str(cfg.get("url") or "").strip()
        probe = (
            _probe_http(url, method="OPTIONS", headers=_dest_auth_headers(cfg))
            if row["enabled"] and row["type"] == DEST_TYPE_HTTPS_POST and url
            else {"url": url or None, "reachable": False, "ok": False, "httpStatus": None, "latencyMs": None, "body": None, "error": None if row["enabled"] else "destination disabled"}
        )
        payload = {
            "destinationId": str(row["id"]),
            "name": row["name"],
            "type": row["type"],
            "enabled": row["enabled"],
            "url": url,
            "apiMode": cfg.get("apiMode"),
            "probe": probe,
            "lastDeliveryStatus": row["last_delivery_status"],
            "lastDeliveryAt": _to_iso8601(row["last_delivery_at"]),
            "lastDeliveryHttpStatus": row["last_delivery_http_status"],
            "lastDeliveryError": row["last_delivery_error"],
        }
        destination_states[str(row["id"])] = payload
        destinations_payload.append(payload)

    routes_by_camera: dict[str, list[dict[str, Any]]] = {}
    for row in route_rows:
        cam_id = str(row["camera_id"])
        dest_id = str(row["destination_id"])
        entry = {
            "destinationId": dest_id,
            "destinationName": row["destination_name"],
            "eventType": row["event_type"],
            "artifactKind": row["artifact_kind"],
            "server": destination_states.get(dest_id),
        }
        routes_by_camera.setdefault(cam_id, []).append(entry)

    camera_links: list[dict[str, Any]] = []
    stale_count = 0
    connected_count = 0
    for row in camera_rows:
        last_probe_at = row["last_probe_at"]
        stale = True
        if isinstance(last_probe_at, datetime):
            stale = (now - last_probe_at).total_seconds() > MONITOR_RECORDER_STALE_SEC
        if stale:
            stale_count += 1
        if row["connected"] is True and not stale:
            connected_count += 1
        camera_links.append(
            {
                "cameraId": str(row["id"]),
                "name": row["name"],
                "cameraStatus": row["status"],
                "cameraToEdge": {
                    "connected": row["connected"],
                    "stale": stale,
                    "lastConnectReason": row["last_connect_reason"],
                    "lastProbeAt": _to_iso8601(last_probe_at),
                    "ringRunning": row["ring_running"],
                    "ringRestartCount": row["ring_restart_count"],
                    "lastRingExitCode": row["last_ring_exit_code"],
                },
                "edgeToServer": routes_by_camera.get(str(row["id"]), []),
            }
        )

    latest_probe_at = None
    valid_probe_times = [r["last_probe_at"] for r in camera_rows if isinstance(r["last_probe_at"], datetime)]
    if valid_probe_times:
        latest_probe_at = max(valid_probe_times)

    return {
        "checkedAt": _to_iso8601(now),
        "edge": {
            "deviceName": socket.gethostname(),
            "api": {"ok": True},
            "database": {"ok": True},
            "recorder": {
                "ok": stale_count < len(camera_rows) if camera_rows else True,
                "connectedCameraCount": connected_count,
                "cameraCount": len(camera_rows),
                "staleCameraCount": stale_count,
                "lastProbeAt": _to_iso8601(latest_probe_at),
                "staleAfterSec": MONITOR_RECORDER_STALE_SEC,
            },
            "dxnnHost": {
                "inferUrl": DXNN_HOST_INFER_URL or None,
                "healthUrl": dxnn_health_url or None,
                **dxnn_probe,
            },
        },
        "destinations": destinations_payload,
        "links": camera_links,
    }


@app.post("/events", status_code=201)
def create_event(body: CreateEventRequest, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (camera_id, event_type, severity, occurred_at, payload_json)
            VALUES (%s, %s, %s, NOW(), %s)
            RETURNING *
            """,
            (str(body.cameraId), body.type, body.severity, json.dumps(body.payload or {})),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row["id"]),
        "cameraId": str(row["camera_id"]),
        "type": row["event_type"],
        "severity": row["severity"],
        "occurredAt": _to_iso8601(row["occurred_at"]),
        "payload": row["payload_json"],
    }


@app.get("/artifacts")
def list_artifacts(
    eventId: Optional[UUID] = None,
    cameraId: Optional[UUID] = None,
    kind: Optional[str] = None,
    _=Depends(require_roles("admin", "operator")),
):
    clauses = []
    values: list[Any] = []
    if eventId:
        clauses.append("a.event_id = %s")
        values.append(str(eventId))
    if cameraId:
        clauses.append("a.camera_id = %s")
        values.append(str(cameraId))
    if kind:
        clauses.append("a.kind = %s")
        values.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              a.*,
              e.event_type,
              e.severity,
              e.occurred_at,
              c.name AS camera_name
            FROM artifacts a
            JOIN events e ON e.id = a.event_id
            JOIN cameras c ON c.id = a.camera_id
            {where}
            ORDER BY a.created_at DESC
            LIMIT 200
            """,
            values,
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "eventId": str(r["event_id"]),
            "cameraId": str(r["camera_id"]),
            "cameraName": r["camera_name"],
            "kind": r["kind"],
            "eventType": r["event_type"],
            "severity": r["severity"],
            "occurredAt": _to_iso8601(r["occurred_at"]),
            "localPath": r["local_path"],
            "uri": r["uri"],
            "mimeType": r["mime_type"],
            "checksumSha256": r["checksum_sha256"],
            "sizeBytes": r["size_bytes"],
            "createdAt": _to_iso8601(r["created_at"]),
        }
        for r in rows
    ]


@app.post("/artifacts/{artifact_id}/redeliver", status_code=202)
def redeliver_artifact(artifact_id: UUID, destinationId: Optional[UUID] = None, _=Depends(require_roles("admin", "operator"))):
    with db_conn() as conn, conn.cursor() as cur:
        if destinationId:
            cur.execute(
                """
                INSERT INTO delivery_attempts (artifact_id, destination_id, status, attempt_no, next_retry_at)
                VALUES (%s, %s, 'queued', 1, NOW())
                """,
                (str(artifact_id), str(destinationId)),
            )
        else:
            cur.execute(
                """
                INSERT INTO delivery_attempts (artifact_id, destination_id, status, attempt_no, next_retry_at)
                SELECT %s, rr.destination_id, 'queued', 1, NOW()
                FROM artifacts a
                JOIN routing_rules rr ON rr.camera_id = a.camera_id AND rr.enabled = TRUE
                WHERE a.id = %s
                """,
                (str(artifact_id), str(artifact_id)),
            )
        conn.commit()
    return {"accepted": True}


@app.post("/artifacts/{artifact_id}/send-test")
def send_artifact_test(
    artifact_id: UUID,
    body: ArtifactSendTestRequest,
    _=Depends(require_roles("admin", "operator")),
):
    return _send_artifact_to_destination_now(str(artifact_id), str(body.destinationId))


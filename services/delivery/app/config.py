import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def resolve_device_name() -> str:
    explicit = os.getenv("EDGE_DEVICE_NAME", "").strip()
    if explicit:
        return explicit
    host_file = os.getenv("EDGE_DEVICE_NAME_FILE", "")
    try:
        txt = Path(host_file).read_text(encoding="utf-8", errors="ignore").strip()
        if txt:
            return txt.splitlines()[0].strip()
    except Exception:
        pass
    return socket.gethostname()


def resolve_system_timezone():
    tz_name = os.getenv("VMS_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


@dataclass(frozen=True)
class DeliverySettings:
    database_url: str = os.getenv("DATABASE_URL", "postgres://vms:vms@postgres:5432/vms?sslmode=disable")
    poll_sec: float = float(os.getenv("DELIVERY_POLL_SEC", "2"))
    timeout_sec: int = int(os.getenv("DELIVERY_TIMEOUT_SEC", "15"))
    delete_local_artifact_on_success: bool = os.getenv("DELETE_LOCAL_ARTIFACT_ON_SUCCESS", "true").lower() in ("1", "true", "yes", "on")
    delete_local_snapshot_on_success: bool = os.getenv("DELETE_LOCAL_SNAPSHOT_ON_SUCCESS", "true").lower() in ("1", "true", "yes", "on")
    backoff: list[int] = field(default_factory=lambda: [5, 15, 30, 60, 120])
    system_tz: timezone = field(default_factory=resolve_system_timezone)
    device_name: str = field(default_factory=resolve_device_name)

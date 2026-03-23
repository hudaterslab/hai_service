import os
import socket
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path


def resolve_device_name() -> str:
    explicit = os.getenv("EDGE_DEVICE_NAME", "").strip()
    if explicit:
        return explicit
    host_file = os.getenv("EDGE_DEVICE_NAME_FILE", "/etc/host_hostname")
    try:
        txt = Path(host_file).read_text(encoding="utf-8", errors="ignore").strip()
        if txt:
            return txt.splitlines()[0].strip()
    except Exception:
        pass
    return socket.gethostname()


@dataclass(frozen=True)
class DeliverySettings:
    database_url: str = os.getenv("DATABASE_URL", "postgres://vms:vms@postgres:5432/vms?sslmode=disable")
    poll_sec: float = float(os.getenv("DELIVERY_POLL_SEC", "2"))
    timeout_sec: int = int(os.getenv("DELIVERY_TIMEOUT_SEC", "15"))
    delete_local_artifact_on_success: bool = os.getenv("DELETE_LOCAL_ARTIFACT_ON_SUCCESS", "true").lower() in ("1", "true", "yes", "on")
    delete_local_snapshot_on_success: bool = os.getenv("DELETE_LOCAL_SNAPSHOT_ON_SUCCESS", "true").lower() in ("1", "true", "yes", "on")
    backoff: list[int] = field(default_factory=lambda: [5, 15, 30, 60, 120])
    kst: timezone = field(default_factory=lambda: timezone(timedelta(hours=9)))
    device_name: str = field(default_factory=resolve_device_name)


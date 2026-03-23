import os
import socket
from dataclasses import dataclass
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
class RecorderSettings:
    media_root: Path
    ffmpeg_bin: str
    use_ffmpeg_artifacts: bool
    enable_rtsp_ring_buffer: bool
    ring_segment_sec: int
    ring_buffer_seconds: int
    kst: timezone
    device_name: str

    @classmethod
    def from_env(cls) -> "RecorderSettings":
        return cls(
            media_root=Path(os.getenv("MEDIA_ROOT", "/var/lib/vms")),
            ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg"),
            use_ffmpeg_artifacts=os.getenv("USE_FFMPEG_ARTIFACTS", "false").lower() == "true",
            enable_rtsp_ring_buffer=os.getenv("ENABLE_RTSP_RING_BUFFER", "false").lower() == "true",
            ring_segment_sec=int(os.getenv("RING_SEGMENT_SEC", "1")),
            ring_buffer_seconds=int(os.getenv("RING_BUFFER_SECONDS", "120")),
            kst=timezone(timedelta(hours=9)),
            device_name=resolve_device_name(),
        )

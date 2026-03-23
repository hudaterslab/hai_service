import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RecorderSettings


def parse_dt(value: Any) -> datetime:
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


def safe_token(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", (text or "").strip(), flags=re.UNICODE).strip("._")
    return cleaned or fallback


def normalize_rotate_deg(value: object) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except Exception:
        return 0
    return n if n in (90, 180, 270) else 0


def rotation_filter_for_ffmpeg(rotate_deg: int) -> str | None:
    deg = normalize_rotate_deg(rotate_deg)
    if deg == 90:
        return "transpose=1"
    if deg == 180:
        return "hflip,vflip"
    if deg == 270:
        return "transpose=2"
    return None


class ArtifactBuilder:
    def __init__(self, settings: RecorderSettings):
        self.settings = settings

    def ensure_dirs(self) -> None:
        (self.settings.media_root / "clips").mkdir(parents=True, exist_ok=True)
        (self.settings.media_root / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.settings.media_root / "ring").mkdir(parents=True, exist_ok=True)

    def build_transfer_label(self, camera_name: str, event_name: str, occurred_at: Any) -> str:
        dt = parse_dt(occurred_at).astimezone(self.settings.kst)
        ts = dt.strftime("%Y%m%d_%H%M%S")
        return f"[{self.settings.device_name}][{camera_name}][{event_name}][{ts}KST]"

    def build_artifact_stem(self, camera_name: str, event_name: str, occurred_at: Any) -> str:
        dt = parse_dt(occurred_at).astimezone(self.settings.kst)
        ts = dt.strftime("%Y%m%d_%H%M%S")
        return f"[{safe_token(self.settings.device_name, 'edge')}][{safe_token(camera_name, 'camera')}][{safe_token(event_name, 'event')}][{ts}KST]"

    def make_placeholder(self, kind: str, camera_id: str, event_id: str, artifact_stem: str | None = None) -> tuple[Path, str]:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = artifact_stem or f"{camera_id}_{event_id}_{now}"
        if kind == "clip":
            path = self.settings.media_root / "clips" / f"{stem}.mp4"
            path.write_bytes(f"fake-clip camera={camera_id} event={event_id} ts={now}\n".encode("utf-8"))
            return path, "video/mp4"
        path = self.settings.media_root / "snapshots" / f"{stem}.jpg"
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            img = np.full((360, 640, 3), 40, dtype=np.uint8)
            cv2.putText(img, "SNAPSHOT UNAVAILABLE", (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 220, 255), 2, cv2.LINE_AA)
            cv2.putText(img, f"camera={camera_id[:8]}", (30, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 1, cv2.LINE_AA)
            cv2.putText(img, f"event={event_id[:8]}", (30, 238), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 1, cv2.LINE_AA)
            cv2.putText(img, now, (30, 266), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 1, cv2.LINE_AA)
            ok, enc = cv2.imencode(".jpg", img)
            path.write_bytes(bytes(enc) if ok else f"placeholder-jpeg-encode-failed camera={camera_id} event={event_id} ts={now}\n".encode("utf-8"))
        except Exception:
            path.write_bytes(f"placeholder-jpeg-fallback camera={camera_id} event={event_id} ts={now}\n".encode("utf-8"))
        return path, "image/jpeg"

    def make_ffmpeg_artifact(
        self,
        kind: str,
        camera_id: str,
        event_id: str,
        rtsp_url: str,
        clip_duration_sec: int,
        rotate_deg: int = 0,
        artifact_stem: str | None = None,
    ) -> tuple[Path | None, str | None]:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = artifact_stem or f"{camera_id}_{event_id}_{now}"
        try:
            if kind == "clip":
                path = self.settings.media_root / "clips" / f"{stem}.mp4"
                cmd = [self.settings.ffmpeg_bin, "-y", "-rtsp_transport", "tcp", "-i", rtsp_url, "-t", str(max(clip_duration_sec, 1)), "-c", "copy", str(path)]
                mime = "video/mp4"
            else:
                path = self.settings.media_root / "snapshots" / f"{stem}.jpg"
                cmd = [self.settings.ffmpeg_bin, "-y", "-rtsp_transport", "tcp", "-i", rtsp_url]
                vf = rotation_filter_for_ffmpeg(rotate_deg)
                if vf:
                    cmd.extend(["-vf", vf])
                cmd.extend(["-frames:v", "1", str(path)])
                mime = "image/jpeg"
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
            if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
                return path, mime
        except Exception:
            pass
        return None, None

    def make_artifact(
        self,
        kind: str,
        camera_id: str,
        event_id: str,
        rtsp_url: str,
        clip_duration_sec: int,
        rotate_deg: int = 0,
        artifact_stem: str | None = None,
    ) -> tuple[Path, str]:
        if self.settings.use_ffmpeg_artifacts:
            path, mime = self.make_ffmpeg_artifact(kind, camera_id, event_id, rtsp_url, clip_duration_sec, rotate_deg=rotate_deg, artifact_stem=artifact_stem)
            if path and mime:
                return path, mime
        return self.make_placeholder(kind, camera_id, event_id, artifact_stem=artifact_stem)


class RingBufferManager:
    def __init__(self, settings: RecorderSettings):
        self.settings = settings
        self.procs: dict[str, subprocess.Popen] = {}
        self.meta: dict[str, dict[str, Any]] = {}

    def ring_dir(self, camera_id: str) -> Path:
        d = self.settings.media_root / "ring" / camera_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def cleanup_old_segments(self, camera_id: str) -> None:
        cutoff = time.time() - max(self.settings.ring_buffer_seconds, 10)
        for p in self.ring_dir(camera_id).glob("*.ts"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except Exception:
                pass

    def ensure_recorder(self, camera: dict[str, Any]) -> None:
        if not (self.settings.use_ffmpeg_artifacts and self.settings.enable_rtsp_ring_buffer):
            return
        cam_id = str(camera["id"])
        proc = self.procs.get(cam_id)
        if proc and proc.poll() is None:
            self.cleanup_old_segments(cam_id)
            return
        out_pattern = str(self.ring_dir(cam_id) / "%Y%m%dT%H%M%SZ.ts")
        cmd = [
            self.settings.ffmpeg_bin,
            "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", camera["rtsp_url"],
            "-an", "-c:v", "copy", "-f", "segment",
            "-segment_time", str(max(self.settings.ring_segment_sec, 1)),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            "-strftime", "1", out_pattern,
        ]
        try:
            self.procs[cam_id] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            meta = self.meta.get(cam_id, {"restart_count": 0, "last_exit_code": None})
            meta["restart_count"] = int(meta.get("restart_count", 0)) + 1
            meta["last_exit_code"] = None
            self.meta[cam_id] = meta
        except Exception:
            pass

    def stop_recorder(self, camera_id: str) -> None:
        proc = self.procs.get(camera_id)
        if not proc:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        else:
            meta = self.meta.get(camera_id, {"restart_count": 0, "last_exit_code": None})
            meta["last_exit_code"] = proc.returncode
            self.meta[camera_id] = meta
        self.procs.pop(camera_id, None)

    def runtime_info(self, camera_id: str) -> dict[str, Any]:
        proc = self.procs.get(camera_id)
        running = bool(proc and proc.poll() is None)
        exit_code = None
        if proc and proc.poll() is not None:
            exit_code = proc.returncode
        meta = self.meta.get(camera_id, {"restart_count": 0, "last_exit_code": None})
        if exit_code is None:
            exit_code = meta.get("last_exit_code")
        return {"running": running, "restart_count": int(meta.get("restart_count", 0)), "last_exit_code": exit_code}

    def parse_segment_time(self, path: Path) -> datetime | None:
        try:
            return datetime.strptime(path.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def build_clip(self, camera_id: str, event_id: str, start_ts: datetime, end_ts: datetime) -> Path | None:
        segs = []
        for p in sorted(self.ring_dir(camera_id).glob("*.ts")):
            ts = self.parse_segment_time(p)
            if ts and start_ts <= ts <= end_ts:
                segs.append(p)
        if not segs:
            return None
        concat_file = self.ring_dir(camera_id) / f"concat_{event_id}.txt"
        try:
            lines = []
            for p in segs:
                escaped = str(p).replace("'", "'\\''")
                lines.append(f"file '{escaped}'")
            concat_file.write_text("\n".join(lines), encoding="utf-8")
            out = self.settings.media_root / "clips" / f"{camera_id}_{event_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.mp4"
            cmd = [self.settings.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(out)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return out
        except Exception:
            return None
        finally:
            try:
                concat_file.unlink(missing_ok=True)
            except Exception:
                pass
        return None

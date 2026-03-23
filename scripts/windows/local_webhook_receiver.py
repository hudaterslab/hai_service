import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import uuid
import re


class Handler(BaseHTTPRequestHandler):
    log_dir: Path = Path("webhook-events")
    log_prefix: str = "webhook-events"
    upload_dir: Path = Path("uploads")
    local_tz = timezone(timedelta(hours=9), name="KST")

    @staticmethod
    def _safe_token(value: str, default: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return default
        token = re.sub(r"[^A-Za-z0-9._-]+", "-", raw)
        token = token.strip("-._")
        return token[:80] if token else default

    @classmethod
    def _format_local_ts(cls, value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return datetime.now(cls.local_tz).strftime("%Y%m%d_%H%M%S")
        try:
            # Handle both "...Z" and "+00:00" styles.
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(cls.local_tz).strftime("%Y%m%d_%H%M%S")
        except Exception:
            return datetime.now(cls.local_tz).strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _parse_disposition(value: str) -> dict:
        out: dict[str, str] = {}
        for part in value.split(";"):
            token = part.strip()
            if "=" in token:
                k, v = token.split("=", 1)
                out[k.strip().lower()] = v.strip().strip('"')
            else:
                out[token.lower()] = ""
        return out

    @staticmethod
    def _extract_boundary(content_type: str) -> bytes:
        for part in content_type.split(";"):
            token = part.strip()
            if token.lower().startswith("boundary="):
                return token.split("=", 1)[1].strip().strip('"').encode("utf-8")
        return b""

    @classmethod
    def _daily_log_path(cls) -> Path:
        date_key = datetime.now(cls.local_tz).strftime("%Y%m%d")
        cls.log_dir.mkdir(parents=True, exist_ok=True)
        return cls.log_dir / f"{cls.log_prefix}-{date_key}.log"

    def _parse_multipart(self, raw: bytes, content_type: str) -> tuple[dict, list[dict]]:
        fields: dict[str, str] = {}
        saved_files: list[dict] = []
        boundary = self._extract_boundary(content_type)
        if not boundary:
            return fields, saved_files
        sep = b"--" + boundary
        parts = raw.split(sep)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        for part in parts:
            chunk = part.strip()
            if not chunk or chunk == b"--":
                continue
            if chunk.startswith(b"--"):
                chunk = chunk[2:]
            if chunk.startswith(b"\r\n"):
                chunk = chunk[2:]
            head, body = (chunk.split(b"\r\n\r\n", 1) + [b""])[:2]
            body = body.rstrip(b"\r\n")
            headers: dict[str, str] = {}
            for line in head.split(b"\r\n"):
                if b":" not in line:
                    continue
                k, v = line.split(b":", 1)
                headers[k.decode("utf-8", errors="ignore").strip().lower()] = v.decode("utf-8", errors="ignore").strip()
            disp = self._parse_disposition(headers.get("content-disposition", ""))
            field_name = disp.get("name", "")
            filename = disp.get("filename", "")
            if filename:
                safe_src = Path(filename).name or f"upload-{uuid.uuid4().hex}.bin"
                ext = Path(safe_src).suffix or ".bin"
                dev_raw = fields.get("deviceName", "") or fields.get("cameraLocation", "") or f"device-{self.client_address[0]}"
                cam = self._safe_token(fields.get("cameraName", ""), "camera")
                ev = self._safe_token(fields.get("eventName", ""), "event")
                dev = self._safe_token(dev_raw, "device")
                ts_raw = fields.get("eventTimestamp", "") or fields.get("occurredAt", "")
                ts = self._format_local_ts(ts_raw)
                out = self.upload_dir / f"[{ev}][{dev}][{cam}]{ts}{ext}"
                idx = 1
                while out.exists():
                    out = self.upload_dir / f"[{ev}][{dev}][{cam}]{ts}_{idx}{ext}"
                    idx += 1
                out.write_bytes(body)
                saved_files.append(
                    {
                        "field": field_name,
                        "filename": filename,
                        "savedPath": str(out.resolve()),
                        "size": len(body),
                    }
                )
            elif field_name:
                fields[field_name] = body.decode("utf-8", errors="ignore")
        return fields, saved_files

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        content_type = self.headers.get("Content-Type", "")
        form_fields, saved_files = self._parse_multipart(raw, content_type) if "multipart/form-data" in content_type else ({}, [])
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": self.path,
            "client": self.client_address[0],
            "headers": {k: v for k, v in self.headers.items()},
            "bodyPreview": raw[:1200].decode("utf-8", errors="ignore"),
            "bodySize": len(raw),
            "formFields": form_fields,
            "savedFiles": saved_files,
        }
        with self._daily_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "savedFiles": saved_files}, ensure_ascii=False).encode("utf-8"))

    def log_message(self, *_):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--log", default="webhook-events")
    ap.add_argument("--log-prefix", default="webhook-events")
    ap.add_argument("--upload-dir", default="uploads")
    args = ap.parse_args()

    log_arg = Path(args.log)
    # Backward compatible: if a legacy "*.log" path is provided, split it into
    # "<parent>/<stem>/" directory + "<stem>-YYYYMMDD.log" files.
    if log_arg.suffix.lower() == ".log":
        Handler.log_dir = (log_arg.resolve().parent / log_arg.stem).resolve()
        Handler.log_prefix = log_arg.stem
    else:
        Handler.log_dir = log_arg.resolve()
        Handler.log_prefix = (args.log_prefix or "webhook-events").strip() or "webhook-events"
    Handler.upload_dir = Path(args.upload_dir).resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"listening http://{args.host}:{args.port} "
        f"logDir={Handler.log_dir} logPattern={Handler.log_prefix}-YYYYMMDD.log "
        f"uploads={Handler.upload_dir}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()

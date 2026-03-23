#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error, request
from uuid import uuid4


DEFAULT_BASE_URL = os.getenv("VMS_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "hf" / "HudatersU_Safety_helmet" / "safety_helmet_251209.dxnn"
)


class ApiClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(f"{self.base_url}{path}", data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"HTTP {exc.code} {exc.reason}: {detail}")
        except error.URLError as exc:
            raise SystemExit(f"Request failed: {exc}")
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("PUT", path, payload)


def _sanitize_path_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return clean or f"video-test-{uuid4().hex[:8]}"


def _run_host_command(args: list[str]) -> None:
    try:
        subprocess.run(args, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"Required command not found: {exc.filename}")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed ({exc.returncode}): {' '.join(args)}")


def _stage_video_for_api_container(video_path: str) -> str:
    src = Path(video_path).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"Video file not found: {src}")
    if not src.is_file():
        raise SystemExit(f"Video path is not a file: {src}")
    remote_path = f"/tmp/video_infer_receiver_test_{uuid4().hex}_{src.name}"
    _run_host_command(["docker", "cp", str(src), f"vms-api:{remote_path}"])
    return remote_path


def _cleanup_staged_video(remote_path: str) -> None:
    subprocess.run(
        ["docker", "exec", "vms-api", "rm", "-f", remote_path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ensure_camera(client: ApiClient, camera_name: str, webrtc_path: str, rtsp_url: str) -> dict[str, Any]:
    cameras = client.get("/cameras")
    for camera in cameras:
        if str(camera.get("name") or "").strip() == camera_name:
            return camera
    return client.post(
        "/cameras",
        {
            "name": camera_name,
            "rtspUrl": rtsp_url,
            "webrtcPath": webrtc_path,
            "enabled": True,
        },
    )


def _ensure_destination(
    client: ApiClient,
    destination_id: str,
    destination_name: str,
    receiver_base_url: str,
    terminal_id: str,
    cctv_id: int,
) -> dict[str, Any]:
    if destination_id:
        destinations = client.get("/destinations")
        for row in destinations:
            if str(row.get("id") or "") == destination_id:
                return row
        raise SystemExit(f"Destination not found: {destination_id}")

    if not receiver_base_url or not terminal_id or cctv_id <= 0:
        raise SystemExit("--destination-id 또는 --receiver-base-url/--terminal-id/--cctv-id 조합이 필요합니다")

    upload_url = receiver_base_url.rstrip("/") + "/api/v1/cctv/img"
    destinations = client.get("/destinations")
    for row in destinations:
        config = row.get("config") or {}
        if (
            str(row.get("name") or "").strip() == destination_name
            and str(config.get("url") or "").strip() == upload_url
            and str(config.get("terminalId") or "").strip() == terminal_id
        ):
            return row

    return client.post(
        "/destinations",
        {
            "name": destination_name,
            "type": "https_post",
            "enabled": True,
            "config": {
                "url": upload_url,
                "apiMode": "cctv_img_v1",
                "terminalId": terminal_id,
                "cctvId": int(cctv_id),
            },
        },
    )


def _configure_camera(client: ApiClient, camera_id: str, model_path: str, confidence: float, poll_sec: int, timeout_sec: int, cooldown_sec: int) -> None:
    client.put(
        f"/cameras/{camera_id}/roi",
        {
            "cameraId": camera_id,
            "enabled": True,
            "zones": [{"name": "zone-1", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
        },
    )
    client.put(
        f"/cameras/{camera_id}/model-settings",
        {
            "enabled": True,
            "modelPath": model_path,
            "confidenceThreshold": confidence,
            "timeoutSec": timeout_sec,
            "pollSec": poll_sec,
            "cooldownSec": cooldown_sec,
            "extra": {},
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="영상 파일을 더미 카메라 경로로 추론하고, 이벤트 발생 시 Receiver로 즉시 전송합니다."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("VMS_API_TOKEN", ""))
    parser.add_argument("--video-path", required=True)
    parser.add_argument("--direct-path", action="store_true", help="docker cp 없이 API가 직접 읽을 수 있는 경로를 그대로 사용합니다.")
    parser.add_argument("--camera-name", default="video-infer-test")
    parser.add_argument("--webrtc-path", default="")
    parser.add_argument("--rtsp-url", default="rtsp://127.0.0.1:8554/video-infer-placeholder")
    parser.add_argument("--model-path", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--event-type", default="helmet_missing_in_roi")
    parser.add_argument("--severity", default="high")
    parser.add_argument("--sample-interval-sec", type=float, default=0.25)
    parser.add_argument("--cooldown-sec", type=float, default=5.0)
    parser.add_argument("--max-triggers", type=int, default=1)
    parser.add_argument("--confidence-threshold", type=float, default=0.35)
    parser.add_argument("--timeout-sec", type=int, default=10)
    parser.add_argument("--poll-sec", type=int, default=2)
    parser.add_argument("--destination-id", default="")
    parser.add_argument("--destination-name", default="video-infer-receiver")
    parser.add_argument("--receiver-base-url", default="")
    parser.add_argument("--terminal-id", default="")
    parser.add_argument("--cctv-id", type=int, default=0)
    parser.add_argument("--payload", default="{}", help="JSON object merged into event payload")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = ApiClient(args.base_url, token=args.token)

    model_path = str(Path(args.model_path).expanduser().resolve())
    if not Path(model_path).exists():
        raise SystemExit(f"Model file not found: {model_path}")

    try:
        extra_payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --payload JSON: {exc}")
    if not isinstance(extra_payload, dict):
        raise SystemExit("--payload must be a JSON object")

    webrtc_path = args.webrtc_path.strip() or _sanitize_path_name(args.camera_name)
    camera = _ensure_camera(client, args.camera_name, webrtc_path, args.rtsp_url)
    destination = _ensure_destination(
        client,
        args.destination_id,
        args.destination_name,
        args.receiver_base_url,
        args.terminal_id,
        args.cctv_id,
    )
    _configure_camera(
        client,
        str(camera["id"]),
        model_path=model_path,
        confidence=float(args.confidence_threshold),
        poll_sec=int(args.poll_sec),
        timeout_sec=int(args.timeout_sec),
        cooldown_sec=max(int(args.cooldown_sec), 0),
    )

    staged_path = args.video_path
    if not args.direct_path:
        staged_path = _stage_video_for_api_container(args.video_path)

    try:
        result = client.post(
            f"/cameras/{camera['id']}/video-infer-send",
            {
                "videoPath": staged_path,
                "destinationId": str(destination["id"]),
                "eventType": args.event_type,
                "severity": args.severity,
                "sampleIntervalSec": float(args.sample_interval_sec),
                "cooldownSec": float(args.cooldown_sec),
                "maxTriggers": int(args.max_triggers),
                "payload": extra_payload,
            },
        )
    finally:
        if not args.direct_path and staged_path.startswith("/tmp/video_infer_receiver_test_"):
            _cleanup_staged_video(staged_path)

    print(
        json.dumps(
            {
                "cameraId": camera["id"],
                "cameraName": camera["name"],
                "destinationId": destination["id"],
                "destinationName": destination["name"],
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

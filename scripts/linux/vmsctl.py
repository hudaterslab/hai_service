#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib import error, parse, request
from uuid import uuid4


DEFAULT_BASE_URL = os.getenv("VMS_API_BASE_URL", "http://127.0.0.1:8080")
DEFAULT_TIMEOUT_SEC = max(float(os.getenv("VMS_API_TIMEOUT_SEC", "60")), 1.0)
KST = timezone(timedelta(hours=9), "KST")
KST_TIME_ONLY_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<millis>\d{1,3}))?)?$")
CLI_EXAMPLES = """Examples:
  vmsctl.py camera list
  vmsctl.py snapshot capture --camera-id CAMERA_ID --event-type helmet_missing_in_roi
  vmsctl.py video capture --camera-id CAMERA_ID --video-path /data/sample.mp4 --offset-sec 3
  vmsctl.py video infer-send --camera-id CAMERA_ID --video-path /data/sample.mp4 --destination-id DEST_ID --event-type helmet_missing_in_roi
  vmsctl.py receiver register --name hanjin --receiver-base-url https://tmlsafety.hudaters.net/receiver --terminal-id 99999 --cctv-id 1
  vmsctl.py receiver capture-send --camera-id CAMERA_ID --destination-id DEST_ID --event-type person_cross_roi
  vmsctl.py monitor overview
  vmsctl.py destination check
  vmsctl.py route check

  vmsctl.py help camera
  vmsctl.py help snapshot
  vmsctl.py help video
  vmsctl.py help receiver
  vmsctl.py help destination
"""

TOPIC_HELP: dict[str, str] = {
    "camera": """카메라 명령:
  camera list
    등록된 카메라 목록과 RTSP URL, WebRTC 경로, 활성화 여부, 현재 상태를 표시합니다.

  camera add --name NAME --rtsp-url URL --webrtc-path PATH [--onvif-profile PROFILE] [--enabled true|false]
    새 카메라를 등록합니다.

  camera update CAMERA_ID [--name NAME] [--rtsp-url URL] [--webrtc-path PATH] [--onvif-profile PROFILE] [--enabled true|false]
    기존 카메라의 일부 항목을 수정합니다.

  camera delete CAMERA_ID
    카메라를 삭제합니다.

예시:
  vmsctl.py camera list
  vmsctl.py camera add --name cam-01 --rtsp-url rtsp://192.168.1.50:554/stream --webrtc-path cam-01
  vmsctl.py camera update 50b4194c-0fc6-4255-81b8-f0eab9ff6926 --enabled false
""",
    "monitor": """모니터링 명령:
  monitor cameras
    카메라-엣지 연결 상태, 링 프로세스 상태, 마지막 연결 사유를 표시합니다.

  monitor overview
    엣지 상태, recorder 상태, DXNN host 상태, 카메라별 라우팅 수를 요약 표시합니다.

예시:
  vmsctl.py monitor cameras
  vmsctl.py monitor overview
""",
    "destination": """목적지 명령:
  destination list
    설정된 라우팅 서버 목적지 목록을 표시합니다.

  destination add --name NAME --url URL --terminal-id ID [--cctv-id N | --cctv-id-map JSON] [--token TOKEN | --token-env ENV]
    `https_post` 목적지를 등록합니다.

  destination update DEST_ID [--name NAME] [--enabled true|false] [--url URL] [--terminal-id ID] [--cctv-id N] [--cctv-id-map JSON]
    목적지 이름, 활성화 상태, 전송 설정을 수정합니다.

  destination delete DEST_ID
    목적지와 연결된 라우팅 규칙을 함께 삭제합니다.

  destination check [--destination-id DEST_ID] [--name NAME]
    목적지 도달 여부와 마지막 전송 상태를 확인합니다.

예시:
  vmsctl.py destination list
  vmsctl.py destination add --name receiver-a --url https://receiver.example.com/upload --terminal-id edge-a --cctv-id 101
  vmsctl.py destination add --name receiver-b --url https://receiver.example.com/upload --terminal-id edge-b --cctv-id-map '{"camera-id": 101}'
  vmsctl.py destination check
""",
    "route": """라우팅 명령:
  route list
    라우팅 규칙 목록을 표시합니다.

  route add --camera-id CAMERA_ID --destination-id DEST_ID [--event-type TYPE] [--artifact-kind clip|snapshot|both] [--enabled true|false]
    카메라와 목적지를 연결하는 라우팅 규칙을 생성합니다.

  route update RULE_ID --enabled true|false
    라우팅 규칙을 활성화 또는 비활성화합니다.

  route delete RULE_ID
    라우팅 규칙을 삭제합니다.

  route check [--camera-id CAMERA_ID]
    카메라에서 서버로의 라우팅 연결 상태를 확인합니다.

예시:
  vmsctl.py route list
  vmsctl.py route add --camera-id 50b4194c-0fc6-4255-81b8-f0eab9ff6926 --destination-id 11111111-1111-1111-1111-111111111111 --event-type motion --artifact-kind snapshot
  vmsctl.py route check
""",
    "snapshot": """스냅샷 명령:
  snapshot capture --camera-id CAMERA_ID [--event-type TYPE] [--severity LEVEL] [--occurred-at ISO] [--payload JSON]
    선택한 카메라에서 스냅샷을 촬영하고, 이벤트/아티팩트로 등록한 뒤 디스크에 저장합니다.

  snapshot list [--camera-id CAMERA_ID]
    저장된 스냅샷 아티팩트 목록을 조회합니다.

예시:
  vmsctl.py snapshot capture --camera-id 50b4194c-0fc6-4255-81b8-f0eab9ff6926 --event-type helmet_missing_in_roi
  vmsctl.py snapshot list --camera-id 50b4194c-0fc6-4255-81b8-f0eab9ff6926
""",
    "video": """동영상 파일 테스트 명령:
  video capture --camera-id CAMERA_ID --video-path PATH [--offset-sec N] [--event-type TYPE] [--severity LEVEL] [--occurred-at ISO] [--payload JSON]
    지정한 동영상 파일에서 특정 시점의 프레임을 추출하고, 기존 카메라 파이프라인과 동일하게 스냅샷 아티팩트로 저장합니다.

  video capture-send --camera-id CAMERA_ID --video-path PATH --destination-id DEST_ID [--offset-sec N] [--event-type TYPE] [--severity LEVEL]
    동영상 파일에서 프레임을 추출해 저장한 뒤, 지정한 Receiver 목적지로 즉시 테스트 전송합니다.

  video infer-send --camera-id CAMERA_ID --video-path PATH --destination-id DEST_ID [--event-type TYPE] [--sample-interval-sec N] [--cooldown-sec N]
    동영상을 시간 순서대로 읽으면서 추론하고, 이벤트가 감지되면 스냅샷 저장과 Receiver 전송을 수행합니다.
    이벤트 발생 후 cooldown 동안은 영상은 계속 소비하지만 추론은 중지합니다.

예시:
  vmsctl.py video capture --camera-id 50b4194c-0fc6-4255-81b8-f0eab9ff6926 --video-path /data/sample.mp4 --offset-sec 5
  vmsctl.py video capture-send --camera-id 50b4194c-0fc6-4255-81b8-f0eab9ff6926 --video-path /data/sample.mp4 --offset-sec 3 --destination-id DEST_ID
  vmsctl.py video infer-send --camera-id 50b4194c-0fc6-4255-81b8-f0eab9ff6926 --video-path /data/sample.mp4 --destination-id DEST_ID --event-type helmet_missing_in_roi
""",
    "receiver": """Receiver 명령:
  receiver register --name NAME --receiver-base-url URL --terminal-id ID [--cctv-id N | --cctv-id-map JSON] [--token TOKEN | --token-env ENV]
    `apiMode=cctv_img_v1` 형식의 Receiver 호환 목적지를 등록합니다.

  receiver list
    Receiver 호환 목적지만 표시합니다.

  receiver send-test --artifact-id ARTIFACT_ID --destination-id DEST_ID
    저장된 스냅샷 아티팩트를 선택한 Receiver 목적지로 즉시 테스트 전송합니다.

  receiver capture-send --camera-id CAMERA_ID --destination-id DEST_ID [--event-type TYPE] [--severity LEVEL]
    카메라에서 스냅샷을 촬영하고 저장한 뒤 즉시 테스트 전송합니다.

예시:
  vmsctl.py receiver register --name hanjin --receiver-base-url https://tmlsafety.hudaters.net/receiver --terminal-id 99999 --cctv-id 1
  vmsctl.py receiver send-test --artifact-id ART_ID --destination-id DEST_ID
  vmsctl.py receiver capture-send --camera-id CAM_ID --destination-id DEST_ID --event-type person_cross_roi
""",
}


def _json_default(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _print_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> None:
    if not rows:
        print("(no rows)")
        return
    widths: list[int] = []
    for key, label in columns:
        width = len(label)
        for row in rows:
            width = max(width, len(_json_default(row.get(key))))
        widths.append(width)
    header = "  ".join(label.ljust(widths[idx]) for idx, (_, label) in enumerate(columns))
    print(header)
    print("  ".join("-" * widths[idx] for idx in range(len(columns))))
    for row in rows:
        print("  ".join(_json_default(row.get(key)).ljust(widths[idx]) for idx, (key, _) in enumerate(columns)))


def _parse_json_arg(raw: Optional[str], *, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {exc}")


@dataclass
class VmsClient:
    base_url: str
    token: str = ""
    username: str = ""
    password: str = ""
    timeout_sec: float = DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.token and self.username and self.password:
            self.token = self._login(self.username, self.password)

    def _login(self, username: str, password: str) -> str:
        payload = self._request("POST", "/auth/login", {"username": username, "password": password}, use_auth=False)
        token = str(payload.get("access_token") or "")
        if not token:
            raise SystemExit("Login succeeded but access_token is missing")
        return token

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None, *, use_auth: bool = True) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if use_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(f"{self.base_url}{path}", data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"HTTP {exc.code} {exc.reason}: {detail}")
        except TimeoutError:
            raise SystemExit(f"Request timed out after {self.timeout_sec:.1f}s")
        except error.URLError as exc:
            raise SystemExit(f"Request failed: {exc}")

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("PATCH", path, payload)

    def delete(self, path: str) -> None:
        self._request("DELETE", path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="vms-8ch-webrtc 운영 CLI입니다. 카메라, 모니터링, 목적지, 라우팅, 스냅샷, Receiver 테스트를 처리합니다.",
        epilog=CLI_EXAMPLES,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="VMS API 기본 URL")
    parser.add_argument("--token", default=os.getenv("VMS_API_TOKEN", ""), help="Bearer 토큰")
    parser.add_argument("--username", default=os.getenv("VMS_API_USERNAME", ""), help="/auth/login 사용자명")
    parser.add_argument("--password", default=os.getenv("VMS_API_PASSWORD", ""), help="/auth/login 비밀번호")
    parser.add_argument("--timeout-sec", type=float, default=DEFAULT_TIMEOUT_SEC, help="API 요청 타임아웃(초)")

    sub = parser.add_subparsers(dest="group", required=True)

    help_cmd = sub.add_parser(
        "help",
        help="명령별 도움말 보기",
        description="명령 그룹별 상세 도움말을 표시합니다.",
        epilog="예시:\n  vmsctl.py help camera",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    help_cmd.add_argument("topic", nargs="?", choices=["camera", "monitor", "destination", "route", "snapshot", "video", "receiver"], help="도움말 주제")

    camera = sub.add_parser(
        "camera",
        help="카메라 설정",
        description="카메라 목록 조회, 등록, 수정, 삭제를 처리합니다.",
        epilog=TOPIC_HELP["camera"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    camera_sub = camera.add_subparsers(dest="action", required=True)
    camera_sub.add_parser("list", help="카메라 목록")
    camera_add = camera_sub.add_parser("add", help="카메라 등록")
    camera_add.add_argument("--name", required=True)
    camera_add.add_argument("--rtsp-url", required=True)
    camera_add.add_argument("--webrtc-path", required=True)
    camera_add.add_argument("--onvif-profile")
    camera_add.add_argument("--enabled", choices=["true", "false"], default="true")
    camera_update = camera_sub.add_parser("update", help="카메라 수정")
    camera_update.add_argument("camera_id")
    camera_update.add_argument("--name")
    camera_update.add_argument("--rtsp-url")
    camera_update.add_argument("--webrtc-path")
    camera_update.add_argument("--onvif-profile")
    camera_update.add_argument("--enabled", choices=["true", "false"])
    camera_delete = camera_sub.add_parser("delete", help="카메라 삭제")
    camera_delete.add_argument("camera_id")

    monitor = sub.add_parser(
        "monitor",
        help="연결 상태",
        description="카메라-엣지, 엣지-서버 연결 상태를 조회합니다.",
        epilog=TOPIC_HELP["monitor"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    monitor_sub = monitor.add_subparsers(dest="action", required=True)
    monitor_sub.add_parser("cameras", help="카메라 연결 상태")
    monitor_sub.add_parser("overview", help="엣지 전체 상태")

    destination = sub.add_parser(
        "destination",
        help="라우팅 서버 목적지",
        description="HTTPS 목적지 등록과 수정, 도달성 점검을 처리합니다.",
        epilog=TOPIC_HELP["destination"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    destination_sub = destination.add_subparsers(dest="action", required=True)
    destination_sub.add_parser("list", help="목적지 목록")
    dest_add = destination_sub.add_parser("add", help="https_post 목적지 등록")
    dest_add.add_argument("--name", required=True)
    dest_add.add_argument("--url", required=True)
    dest_add.add_argument("--terminal-id", required=True)
    dest_add.add_argument("--cctv-id", type=int)
    dest_add.add_argument("--cctv-id-map", help='JSON object, e.g. {"camera-id": 101}')
    dest_add.add_argument("--token")
    dest_add.add_argument("--token-env")
    dest_add.add_argument("--enabled", choices=["true", "false"], default="true")
    dest_update = destination_sub.add_parser("update", help="목적지 수정")
    dest_update.add_argument("destination_id")
    dest_update.add_argument("--name")
    dest_update.add_argument("--url")
    dest_update.add_argument("--terminal-id")
    dest_update.add_argument("--cctv-id", type=int)
    dest_update.add_argument("--cctv-id-map", help='JSON object, e.g. {"camera-id": 101}')
    dest_update.add_argument("--token")
    dest_update.add_argument("--token-env")
    dest_update.add_argument("--enabled", choices=["true", "false"])
    dest_update.add_argument("--preserve-config", action="store_true", help="Keep current config unless fields above are passed")
    dest_delete = destination_sub.add_parser("delete", help="목적지 삭제")
    dest_delete.add_argument("destination_id")
    dest_check = destination_sub.add_parser("check", help="목적지 연결 상태 확인")
    dest_check.add_argument("--destination-id")
    dest_check.add_argument("--name")

    route = sub.add_parser(
        "route",
        help="카메라-목적지 라우팅",
        description="라우팅 규칙 생성, 수정, 삭제, 점검을 처리합니다.",
        epilog=TOPIC_HELP["route"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    route_sub = route.add_subparsers(dest="action", required=True)
    route_sub.add_parser("list", help="라우팅 목록")
    route_add = route_sub.add_parser("add", help="라우팅 규칙 등록")
    route_add.add_argument("--camera-id", required=True)
    route_add.add_argument("--destination-id", required=True)
    route_add.add_argument("--event-type", default="*")
    route_add.add_argument("--artifact-kind", choices=["clip", "snapshot", "both"], default="both")
    route_add.add_argument("--enabled", choices=["true", "false"], default="true")
    route_update = route_sub.add_parser("update", help="라우팅 활성/비활성")
    route_update.add_argument("rule_id")
    route_update.add_argument("--enabled", choices=["true", "false"], required=True)
    route_delete = route_sub.add_parser("delete", help="라우팅 삭제")
    route_delete.add_argument("rule_id")
    route_check = route_sub.add_parser("check", help="서버 라우팅 상태 확인")
    route_check.add_argument("--camera-id")

    snapshot = sub.add_parser(
        "snapshot",
        help="스냅샷 촬영/저장",
        description="카메라에서 스냅샷을 촬영해 아티팩트로 저장합니다.",
        epilog=TOPIC_HELP["snapshot"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    snapshot_sub = snapshot.add_subparsers(dest="action", required=True)
    snapshot_capture = snapshot_sub.add_parser("capture", help="스냅샷 촬영 후 저장")
    snapshot_capture.add_argument("--camera-id", required=True)
    snapshot_capture.add_argument("--event-type", default="manual_snapshot")
    snapshot_capture.add_argument("--severity", default="low")
    snapshot_capture.add_argument("--occurred-at", help="ISO 8601 또는 KST 시각(HH:MM[:SS[.mmm]])")
    snapshot_capture.add_argument("--payload", help='JSON object, e.g. {"note":"manual test"}')
    snapshot_list = snapshot_sub.add_parser("list", help="저장된 스냅샷 목록")
    snapshot_list.add_argument("--camera-id")

    video = sub.add_parser(
        "video",
        help="동영상 파일 테스트",
        description="동영상 파일의 프레임을 기존 스냅샷/전송 파이프라인으로 태웁니다.",
        epilog=TOPIC_HELP["video"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    video_sub = video.add_subparsers(dest="action", required=True)
    video_capture = video_sub.add_parser("capture", help="동영상 파일에서 프레임 추출 후 저장")
    video_capture.add_argument("--camera-id", required=True)
    video_capture.add_argument("--video-path", required=True)
    video_capture.add_argument("--direct-path", action="store_true", help="video-path를 컨테이너에서 바로 보이는 경로로 간주하고 복사하지 않습니다")
    video_capture.add_argument("--offset-sec", type=float, default=0.0)
    video_capture.add_argument("--event-type", default="manual_snapshot")
    video_capture.add_argument("--severity", default="low")
    video_capture.add_argument("--occurred-at", help="ISO 8601 또는 KST 시각(HH:MM[:SS[.mmm]])")
    video_capture.add_argument("--payload", help='JSON 객체, 예: {"note":"video test"}')
    video_capture_send = video_sub.add_parser("capture-send", help="동영상 프레임 저장 후 즉시 테스트 전송")
    video_capture_send.add_argument("--camera-id", required=True)
    video_capture_send.add_argument("--video-path", required=True)
    video_capture_send.add_argument("--direct-path", action="store_true", help="video-path를 컨테이너에서 바로 보이는 경로로 간주하고 복사하지 않습니다")
    video_capture_send.add_argument("--destination-id", required=True)
    video_capture_send.add_argument("--offset-sec", type=float, default=0.0)
    video_capture_send.add_argument("--event-type", default="manual_snapshot")
    video_capture_send.add_argument("--severity", default="low")
    video_capture_send.add_argument("--occurred-at", help="ISO 8601 또는 KST 시각(HH:MM[:SS[.mmm]])")
    video_capture_send.add_argument("--payload", help='JSON 객체, 예: {"note":"video test"}')
    video_infer_send = video_sub.add_parser("infer-send", help="동영상을 순차 추론하고 트리거 시 전송")
    video_infer_send.add_argument("--camera-id", required=True)
    video_infer_send.add_argument("--video-path", required=True)
    video_infer_send.add_argument("--direct-path", action="store_true", help="video-path를 컨테이너에서 바로 보이는 경로로 간주하고 복사하지 않습니다")
    video_infer_send.add_argument("--destination-id", required=True)
    video_infer_send.add_argument("--event-type", default="helmet_missing_in_roi")
    video_infer_send.add_argument("--severity", default="high")
    video_infer_send.add_argument("--start-offset-sec", type=float, default=0.0)
    video_infer_send.add_argument("--end-offset-sec", type=float)
    video_infer_send.add_argument("--sample-interval-sec", type=float, default=0.25)
    video_infer_send.add_argument("--cooldown-sec", type=float, default=5.0)
    video_infer_send.add_argument("--max-triggers", type=int, default=1)
    video_infer_send.add_argument("--payload", help='JSON 객체, 예: {"eventName":"안전모 미착용"}')

    receiver = sub.add_parser(
        "receiver",
        help="Receiver 테스트",
        description="Receiver 목적지를 등록하고 저장된 스냅샷을 테스트 전송합니다.",
        epilog=TOPIC_HELP["receiver"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    receiver_sub = receiver.add_subparsers(dest="action", required=True)
    receiver_sub.add_parser("list", help="Receiver 목적지 목록")
    receiver_register = receiver_sub.add_parser("register", help="Receiver 목적지 등록")
    receiver_register.add_argument("--name", required=True)
    receiver_register.add_argument("--receiver-base-url", required=True)
    receiver_register.add_argument("--terminal-id", required=True)
    receiver_register.add_argument("--cctv-id", type=int)
    receiver_register.add_argument("--cctv-id-map", help='JSON object, e.g. {"camera-id": 101}')
    receiver_register.add_argument("--token")
    receiver_register.add_argument("--token-env")
    receiver_register.add_argument("--enabled", choices=["true", "false"], default="true")
    receiver_send = receiver_sub.add_parser("send-test", help="저장된 아티팩트 즉시 테스트 전송")
    receiver_send.add_argument("--artifact-id", required=True)
    receiver_send.add_argument("--destination-id", required=True)
    receiver_capture_send = receiver_sub.add_parser("capture-send", help="촬영/저장 후 즉시 테스트 전송")
    receiver_capture_send.add_argument("--camera-id", required=True)
    receiver_capture_send.add_argument("--destination-id", required=True)
    receiver_capture_send.add_argument("--event-type", default="manual_snapshot")
    receiver_capture_send.add_argument("--severity", default="low")
    receiver_capture_send.add_argument("--occurred-at", help="ISO 8601 또는 KST 시각(HH:MM[:SS[.mmm]])")
    receiver_capture_send.add_argument("--payload", help='JSON object, e.g. {"note":"receiver smoke test"}')

    return parser


def _bool_text(value: str) -> bool:
    return value.lower() == "true"


def _destination_config_from_args(args: argparse.Namespace, *, current: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    current = dict(current or {})
    touched = False
    if getattr(args, "url", None):
        current["url"] = args.url
        touched = True
    if getattr(args, "terminal_id", None):
        current["terminalId"] = args.terminal_id
        touched = True
    if getattr(args, "cctv_id", None) is not None:
        current["cctvId"] = args.cctv_id
        touched = True
    if getattr(args, "cctv_id_map", None):
        current["cctvIdByCameraId"] = _parse_json_arg(args.cctv_id_map, default={})
        touched = True
    token = getattr(args, "token", None)
    token_env = getattr(args, "token_env", None)
    if token or token_env:
        auth = {"type": "bearer"}
        if token_env:
            auth["token_env"] = token_env
        elif token:
            auth["token"] = token
        current["auth"] = auth
        touched = True
    if touched or not getattr(args, "preserve_config", False):
        current["apiMode"] = "cctv_img_v1"
    return current if touched or not getattr(args, "preserve_config", False) else None


def _normalize_receiver_upload_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise SystemExit("base-url is required")
    if base.endswith("/api/v1/cctv/img"):
        return base
    if base.endswith("/receiver"):
        return f"{base}/api/v1/cctv/img"
    return f"{base}/api/v1/cctv/img"


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
    remote_path = f"/tmp/vmsctl_video_{uuid4().hex}_{src.name}"
    _run_host_command(["docker", "cp", str(src), f"vms-api:{remote_path}"])
    return remote_path


def _cleanup_staged_video(remote_path: str) -> None:
    try:
        subprocess.run(
            ["docker", "exec", "vms-api", "rm", "-f", remote_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _normalize_occurred_at_arg(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    matched = KST_TIME_ONLY_RE.fullmatch(raw)
    if matched:
        now_kst = datetime.now(KST)
        second = int(matched.group("second") or "0")
        millis_raw = matched.group("millis") or "0"
        millis = int(millis_raw.ljust(3, "0")[:3])
        dt = now_kst.replace(
            hour=int(matched.group("hour")),
            minute=int(matched.group("minute")),
            second=second,
            microsecond=millis * 1000,
        )
        return dt.isoformat(timespec="milliseconds")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST).isoformat(timespec="milliseconds")


def handle_camera(client: VmsClient, args: argparse.Namespace) -> None:
    if args.action == "list":
        rows = client.get("/cameras")
        _print_table(rows, [
            ("id", "id"),
            ("name", "name"),
            ("status", "status"),
            ("enabled", "enabled"),
            ("webrtcPath", "webrtcPath"),
            ("rtspUrl", "rtspUrl"),
        ])
        return
    if args.action == "add":
        payload = {
            "name": args.name,
            "rtspUrl": args.rtsp_url,
            "webrtcPath": args.webrtc_path,
            "onvifProfile": args.onvif_profile,
            "enabled": _bool_text(args.enabled),
        }
        _print_json(client.post("/cameras", payload))
        return
    if args.action == "update":
        payload = {}
        if args.name is not None:
            payload["name"] = args.name
        if args.rtsp_url is not None:
            payload["rtspUrl"] = args.rtsp_url
        if args.webrtc_path is not None:
            payload["webrtcPath"] = args.webrtc_path
        if args.onvif_profile is not None:
            payload["onvifProfile"] = args.onvif_profile
        if args.enabled is not None:
            payload["enabled"] = _bool_text(args.enabled)
        if not payload:
            raise SystemExit("camera update requires at least one field")
        _print_json(client.patch(f"/cameras/{args.camera_id}", payload))
        return
    if args.action == "delete":
        client.delete(f"/cameras/{args.camera_id}")
        print("deleted")
        return


def handle_monitor(client: VmsClient, args: argparse.Namespace) -> None:
    if args.action == "cameras":
        rows = client.get("/monitor/cameras")
        _print_table(rows, [
            ("cameraId", "cameraId"),
            ("name", "name"),
            ("status", "status"),
            ("connected", "connected"),
            ("ringRunning", "ringRunning"),
            ("ringRestartCount", "ringRestarts"),
            ("lastConnectReason", "lastReason"),
        ])
        return
    if args.action == "overview":
        data = client.get("/monitor/overview")
        edge = data.get("edge", {})
        recorder = edge.get("recorder", {})
        dxnn = edge.get("dxnnHost", {})
        print(f"device={edge.get('deviceName')}")
        print(f"recorder_ok={recorder.get('ok')} connected={recorder.get('connectedCameraCount')}/{recorder.get('cameraCount')} stale={recorder.get('staleCameraCount')}")
        print(f"dxnn_ok={dxnn.get('ok')} reachable={dxnn.get('reachable')} status={dxnn.get('httpStatus')} latency_ms={dxnn.get('latencyMs')}")
        print("")
        rows = []
        for link in data.get("links", []):
            cam_edge = link.get("cameraToEdge", {})
            rows.append({
                "camera": link.get("name"),
                "cameraStatus": link.get("cameraStatus"),
                "connected": cam_edge.get("connected"),
                "stale": cam_edge.get("stale"),
                "lastReason": cam_edge.get("lastConnectReason"),
                "routes": len(link.get("edgeToServer") or []),
            })
        _print_table(rows, [
            ("camera", "camera"),
            ("cameraStatus", "cameraStatus"),
            ("connected", "connected"),
            ("stale", "stale"),
            ("routes", "routes"),
            ("lastReason", "lastReason"),
        ])
        return


def handle_destination(client: VmsClient, args: argparse.Namespace) -> None:
    if args.action == "list":
        rows = client.get("/destinations")
        _print_table(rows, [
            ("id", "id"),
            ("name", "name"),
            ("type", "type"),
            ("enabled", "enabled"),
            ("config", "config"),
        ])
        return
    if args.action == "add":
        payload = {
            "name": args.name,
            "type": "https_post",
            "enabled": _bool_text(args.enabled),
            "config": _destination_config_from_args(args, current={}),
        }
        _print_json(client.post("/destinations", payload))
        return
    if args.action == "update":
        payload: dict[str, Any] = {}
        if args.name is not None:
            payload["name"] = args.name
        if args.enabled is not None:
            payload["enabled"] = _bool_text(args.enabled)
        if not args.preserve_config:
            current_rows = client.get("/destinations")
            current = next((row for row in current_rows if row.get("id") == args.destination_id), None)
            if current is None:
                raise SystemExit("destination not found")
            config = _destination_config_from_args(args, current=current.get("config") or {})
            if config is not None:
                payload["config"] = config
        else:
            config = _destination_config_from_args(args, current={})
            if config is not None:
                payload["config"] = config
        if not payload:
            raise SystemExit("destination update requires at least one field")
        _print_json(client.patch(f"/destinations/{args.destination_id}", payload))
        return
    if args.action == "delete":
        client.delete(f"/destinations/{args.destination_id}")
        print("deleted")
        return
    if args.action == "check":
        overview = client.get("/monitor/overview")
        rows = overview.get("destinations", [])
        if args.destination_id:
            rows = [row for row in rows if row.get("destinationId") == args.destination_id]
        if args.name:
            rows = [row for row in rows if row.get("name") == args.name]
        printable = []
        for row in rows:
            probe = row.get("probe") or {}
            printable.append({
                "destinationId": row.get("destinationId"),
                "name": row.get("name"),
                "enabled": row.get("enabled"),
                "url": row.get("url"),
                "reachable": probe.get("reachable"),
                "ok": probe.get("ok"),
                "httpStatus": probe.get("httpStatus"),
                "latencyMs": probe.get("latencyMs"),
                "lastDeliveryStatus": row.get("lastDeliveryStatus"),
            })
        _print_table(printable, [
            ("destinationId", "destinationId"),
            ("name", "name"),
            ("enabled", "enabled"),
            ("reachable", "reachable"),
            ("ok", "ok"),
            ("httpStatus", "httpStatus"),
            ("latencyMs", "latencyMs"),
            ("lastDeliveryStatus", "lastDeliveryStatus"),
            ("url", "url"),
        ])
        return


def handle_route(client: VmsClient, args: argparse.Namespace) -> None:
    if args.action == "list":
        rows = client.get("/routing-rules")
        _print_table(rows, [
            ("id", "id"),
            ("cameraId", "cameraId"),
            ("eventType", "eventType"),
            ("artifactKind", "artifactKind"),
            ("destinationId", "destinationId"),
            ("enabled", "enabled"),
        ])
        return
    if args.action == "add":
        payload = {
            "cameraId": args.camera_id,
            "destinationId": args.destination_id,
            "eventType": args.event_type,
            "artifactKind": args.artifact_kind,
            "enabled": _bool_text(args.enabled),
        }
        _print_json(client.post("/routing-rules", payload))
        return
    if args.action == "update":
        _print_json(client.patch(f"/routing-rules/{args.rule_id}", {"enabled": _bool_text(args.enabled)}))
        return
    if args.action == "delete":
        client.delete(f"/routing-rules/{args.rule_id}")
        print("deleted")
        return
    if args.action == "check":
        overview = client.get("/monitor/overview")
        rows = []
        for link in overview.get("links", []):
            if args.camera_id and link.get("cameraId") != args.camera_id:
                continue
            for route in link.get("edgeToServer") or []:
                server = route.get("server") or {}
                probe = server.get("probe") or {}
                rows.append({
                    "camera": link.get("name"),
                    "destination": route.get("destinationName"),
                    "eventType": route.get("eventType"),
                    "artifactKind": route.get("artifactKind"),
                    "reachable": probe.get("reachable"),
                    "ok": probe.get("ok"),
                    "httpStatus": probe.get("httpStatus"),
                    "latencyMs": probe.get("latencyMs"),
                })
        _print_table(rows, [
            ("camera", "camera"),
            ("destination", "destination"),
            ("eventType", "eventType"),
            ("artifactKind", "artifactKind"),
            ("reachable", "reachable"),
            ("ok", "ok"),
            ("httpStatus", "httpStatus"),
            ("latencyMs", "latencyMs"),
        ])
        return


def handle_snapshot(client: VmsClient, args: argparse.Namespace) -> None:
    if args.action == "capture":
        payload = {
            "eventType": args.event_type,
            "severity": args.severity,
            "payload": _parse_json_arg(args.payload, default={}),
        }
        if args.occurred_at:
            payload["occurredAt"] = _normalize_occurred_at_arg(args.occurred_at)
        _print_json(client.post(f"/cameras/{args.camera_id}/snapshot-artifact", payload))
        return
    if args.action == "list":
        query: dict[str, str] = {"kind": "snapshot"}
        if args.camera_id:
            query["cameraId"] = args.camera_id
        rows = client.get(f"/artifacts?{parse.urlencode(query)}")
        _print_table(rows, [
            ("id", "id"),
            ("cameraName", "camera"),
            ("eventType", "eventType"),
            ("severity", "severity"),
            ("createdAt", "createdAt"),
            ("localPath", "localPath"),
        ])
        return


def handle_video(client: VmsClient, args: argparse.Namespace) -> None:
    staged_path = ""
    try:
        video_path = args.video_path if getattr(args, "direct_path", False) else _stage_video_for_api_container(args.video_path)
        staged_path = "" if getattr(args, "direct_path", False) else video_path
        if args.action == "infer-send":
            payload = {
                "videoPath": video_path,
                "destinationId": args.destination_id,
                "eventType": args.event_type,
                "severity": args.severity,
                "startOffsetSec": args.start_offset_sec,
                "endOffsetSec": args.end_offset_sec,
                "sampleIntervalSec": args.sample_interval_sec,
                "cooldownSec": args.cooldown_sec,
                "maxTriggers": args.max_triggers,
                "payload": _parse_json_arg(args.payload, default={}),
            }
            _print_json(client.post(f"/cameras/{args.camera_id}/video-infer-send", payload))
            return

        payload = {
            "videoPath": video_path,
            "offsetSec": args.offset_sec,
            "eventType": args.event_type,
            "severity": args.severity,
            "payload": _parse_json_arg(args.payload, default={}),
        }
        if args.occurred_at:
            payload["occurredAt"] = _normalize_occurred_at_arg(args.occurred_at)
        artifact = client.post(f"/cameras/{args.camera_id}/video-snapshot-artifact", payload)
        if args.action == "capture":
            _print_json(artifact)
            return
        if args.action == "capture-send":
            result = client.post(f"/artifacts/{artifact['artifactId']}/send-test", {"destinationId": args.destination_id})
            _print_json({"artifact": artifact, "delivery": result})
            return
    finally:
        if staged_path:
            _cleanup_staged_video(staged_path)


def handle_receiver(client: VmsClient, args: argparse.Namespace) -> None:
    if args.action == "list":
        rows = [
            row
            for row in client.get("/destinations")
            if isinstance(row.get("config"), dict) and str((row.get("config") or {}).get("apiMode") or "").strip().lower() == "cctv_img_v1"
        ]
        _print_table(rows, [
            ("id", "id"),
            ("name", "name"),
            ("enabled", "enabled"),
            ("config", "config"),
        ])
        return
    if args.action == "register":
        wrapped = argparse.Namespace(
            url=_normalize_receiver_upload_url(args.receiver_base_url),
            terminal_id=args.terminal_id,
            cctv_id=args.cctv_id,
            cctv_id_map=args.cctv_id_map,
            token=args.token,
            token_env=args.token_env,
            preserve_config=False,
        )
        payload = {
            "name": args.name,
            "type": "https_post",
            "enabled": _bool_text(args.enabled),
            "config": _destination_config_from_args(wrapped, current={}),
        }
        _print_json(client.post("/destinations", payload))
        return
    if args.action == "send-test":
        _print_json(client.post(f"/artifacts/{args.artifact_id}/send-test", {"destinationId": args.destination_id}))
        return
    if args.action == "capture-send":
        capture_payload = {
            "eventType": args.event_type,
            "severity": args.severity,
            "payload": _parse_json_arg(args.payload, default={}),
        }
        if args.occurred_at:
            capture_payload["occurredAt"] = _normalize_occurred_at_arg(args.occurred_at)
        artifact = client.post(f"/cameras/{args.camera_id}/snapshot-artifact", capture_payload)
        result = client.post(f"/artifacts/{artifact['artifactId']}/send-test", {"destinationId": args.destination_id})
        _print_json({"artifact": artifact, "delivery": result})
        return


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.group == "help":
        if args.topic:
            print(TOPIC_HELP[args.topic].rstrip())
        else:
            parser.print_help()
        return 0
    client = VmsClient(
        base_url=args.base_url,
        token=args.token,
        username=args.username,
        password=args.password,
        timeout_sec=max(float(args.timeout_sec), 1.0),
    )
    handlers = {
        "camera": handle_camera,
        "monitor": handle_monitor,
        "destination": handle_destination,
        "route": handle_route,
        "snapshot": handle_snapshot,
        "video": handle_video,
        "receiver": handle_receiver,
    }
    handlers[args.group](client, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

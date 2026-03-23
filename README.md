# VMS 8CH WebRTC

8채널 규모의 엣지형 VMS(Video Management System) 프로젝트입니다.  
Docker Compose 기반으로 카메라 등록, WebRTC 라이브 뷰, ROI 설정, 이벤트 생성, 클립/스냅샷 아티팩트 생성, 외부 서버 전달까지 한 번에 운영할 수 있도록 구성되어 있습니다.

현재 저장소는 완성형 상용 제품보다는 설치형 엣지 VMS를 빠르게 검증하고 운영 화면을 고도화하기 위한 실전형 프로젝트 구조에 가깝습니다.

## 1. 핵심 기능

- 최대 8대 카메라 관리
- RTSP 입력 수집 및 MediaMTX 기반 WebRTC 라이브 뷰
- 수동 등록 + 네트워크 스캔 기반 카메라 탐색
- ROI(관심 영역) 설정
- 이벤트 정책 관리
- `clip`
- `snapshot`
- AI 모델 연동 기반 이벤트 트리거
- 이벤트 팩(`config/event_packs/`) 기반 룰 설정
- 이벤트 발생 시 아티팩트 생성
- 클립
- 스냅샷
- 외부 목적지 전송
- HTTPS POST
- 일부 코드 경로상 SFTP 지원
- 운영 UI 제공
- 이벤트 로그
- 카메라 상태
- 정책/라우팅 관리
- 추론 점검
- 네트워크/수신 서버 설정

## 2. 전체 구성

기본 배포는 아래 서비스들로 구성됩니다.

- `postgres`
  메타데이터 저장소
- `redis`
  큐/상태/보조 저장소
- `mediamtx`
  RTSP ingest 및 WebRTC 엔드포인트
- `dxnn-host-infer`
  DXRT/DXNN 추론 브리지
- `vms-api`
  FastAPI 기반 REST API + 정적 운영 UI
- `event-recorder`
  카메라 연결 확인, 이벤트 생성, 클립/스냅샷 생성
- `delivery-worker`
  전송 큐 처리 및 외부 서버 전달
- `vms-ops`
  `vmsctl` CLI 실행용 보조 컨테이너

## 3. 처리 흐름

1. 사용자가 카메라를 등록합니다.
2. `event-recorder`가 RTSP 연결 상태를 점검하고 상태를 갱신합니다.
3. 수동 이벤트 또는 AI 기반 이벤트가 생성됩니다.
4. 정책에 따라 클립/스냅샷이 생성됩니다.
5. `delivery-worker`가 라우팅 규칙에 맞춰 목적지로 전달합니다.

데이터 모델 기준 핵심 엔터티는 `cameras`, `event_policies`, `camera_rois`, `events`, `artifacts`, `destinations`, `routing_rules`, `delivery_attempts` 입니다.

## 4. 주요 디렉터리

- `config/`
  런타임 설정 및 이벤트 팩
- `db/`
  스키마, 마이그레이션, ERD 문서
- `deploy/`
  Docker Compose, `.env`, MediaMTX 설정
- `docs/`
  운영/UI/설치 문서
- `models/`
  모델 파일 및 관련 런타임 자산
- `openapi/`
  API 초안 문서
- `runtime/`
  실행 중 생성되는 데이터 루트
- `scripts/linux/`
  설치, 배포, 업데이트, 운영 스크립트
- `services/api/`
  FastAPI 앱 및 운영 UI
- `services/recorder/`
  이벤트/아티팩트 생성 워커
- `services/delivery/`
  외부 전송 워커
- `services/dxnn-host/`
  추론 브리지 컨테이너

## 5. 실행 환경

권장 환경:

- Linux 호스트
- Docker Engine
- Docker Compose Plugin
- RTSP로 접근 가능한 카메라

DXRT/DeepX 런타임을 함께 사용할 경우 추가로 필요합니다.

- `DXRT_HOST_DIR`
- `DXRT_HOST_LIB_DIR`

기본 예시는 [`deploy/.env.example`](./deploy/.env.example)에 정의되어 있습니다.

## 6. 빠른 시작

### 6.1 환경 파일 생성

```bash
cd /media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc
./scripts/linux/deploy-stack.sh init
```

필요 시 `deploy/.env`에서 아래 값을 수정합니다.

- `VMS_DATA_ROOT`
- `DXRT_HOST_DIR`
- `DXRT_HOST_LIB_DIR`
- `DELIVERY_DEDUP_WINDOW_SEC`

### 6.2 호스트 준비

신규 Ubuntu 호스트라면:

```bash
sudo ./scripts/linux/prepare-host.sh
```

Docker 또는 DXRT만 선택적으로 건너뛸 수 있습니다.

```bash
sudo ./scripts/linux/prepare-host.sh --skip-docker
sudo ./scripts/linux/prepare-host.sh --skip-dxrt
```

### 6.3 스택 시작

기존 이미지를 사용해 실행:

```bash
VMS_SKIP_BUILD=true ./scripts/linux/deploy-stack.sh up
```

현재 워크스페이스에서 직접 다시 빌드하려면 명시적으로 허용해야 합니다.

```bash
VMS_ALLOW_LOCAL_BUILD=true ./scripts/linux/deploy-stack.sh up
```

### 6.4 UI 접속

- `http://127.0.0.1:8080/`

### 6.5 상태 확인 / 로그 / 종료

```bash
./scripts/linux/deploy-stack.sh status
./scripts/linux/deploy-stack.sh logs
./scripts/linux/deploy-stack.sh down
```

## 7. 운영 CLI

API 이미지에는 `vmsctl`이 포함되어 있어 컨테이너 내부에서 운영 명령을 실행할 수 있습니다.

```bash
./scripts/linux/deploy-stack.sh ctl --help
./scripts/linux/deploy-stack.sh ctl monitor overview
./scripts/linux/deploy-stack.sh ctl camera list
./scripts/linux/deploy-stack.sh ctl destination check
```

## 8. 기본 포트

- `8080`: API + 운영 UI
- `8554`: RTSP ingest
- `8889`: MediaMTX WebRTC HTTP
- `8189/udp`: WebRTC ICE/UDP
- `18081`: DXNN host infer

## 9. 설정 파일

### `deploy/.env`

배포 경로와 호스트 라이브러리 경로를 정의합니다.

### `config/vms.example.yaml`

다음 운영 설정을 포함합니다.

- 서버 이름 / 시간대
- 최대 카메라 수
- WebRTC 설정
- 저장 경로 및 보관 기간
- 이벤트 정책 기본값
- 전달 대상 및 재시도 정책
- 라우팅 규칙

실운영에서는 `vms.example.yaml`을 복사해 호스트 전용 `vms.yaml`로 대체해서 사용하는 방식이 적합합니다.

### `deploy/mediamtx.yml`

RTSP/WebRTC 게이트웨이 설정 파일입니다.

## 10. 저장 데이터

기본 런타임 데이터는 아래 경로를 사용합니다.

- `runtime/media`
- `runtime/redis`

PostgreSQL은 기본 Compose 설정에서 Docker named volume `vms_pg_data`를 사용합니다.

## 11. 설치/배포 스크립트

### NAS 배포용 설치

```bash
sudo ./install.sh
```

기능:

1. `deploy/.env` 생성
2. 호스트 준비
3. 번들 이미지 로드
4. 스택 기동 및 기본 상태 점검

### 패치 업데이트

```bash
./update.sh --source <rsync-source>
```

### 설치 패키지 생성

```bash
./scripts/linux/build-installer-package.sh
./scripts/linux/build-installer-package.sh --include-images
```

### rsync 배포 트리 생성

```bash
./scripts/linux/build-rsync-release.sh
```

## 12. UI에서 가능한 작업

운영 UI 기준으로 확인되는 주요 기능은 아래와 같습니다.

- 관리자 인증
- 카메라 수동 등록/삭제
- 카메라 자동 탐색 및 자동 등록
- 라이브 뷰 확인
- ROI 도형 편집
- 이벤트 팩 설정
- 이벤트 정책 저장
- 전송 목적지/라우팅 설정
- 최근 이벤트 및 아티팩트 확인
- 사람 감지 규칙 및 수동 이벤트 테스트
- 네트워크 설정 및 수신 서버 설정
- 추론 상태 점검

## 13. 참고 문서

- [`docs/README.md`](./docs/README.md)
- [`docs/OPERATIONS_GUIDE_KO.md`](./docs/OPERATIONS_GUIDE_KO.md)
- [`WEBUI_MANUAL_KO_2026-03-10.md`](./WEBUI_MANUAL_KO_2026-03-10.md)
- [`docs/SW_PIPELINE_ARCHITECTURE_KO.md`](./docs/SW_PIPELINE_ARCHITECTURE_KO.md)
- [`docs/NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`](./docs/NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md)
- [`db/erd.md`](./db/erd.md)

## 14. 현재 프로젝트 성격

이 저장소는 다음 목적에 특히 적합합니다.

- 엣지 장비용 VMS 아키텍처 검증
- 8채널급 카메라 이벤트 파이프라인 실험
- WebRTC 기반 현장 운영 UI 개발
- AI 이벤트/ROI/전송 정책 통합 테스트

반면, 대규모 멀티노드 운영, 강한 인증/권한 체계, 완전한 상용 배포 자동화는 추가 보강이 필요한 상태입니다.

# VMS 운영 가이드

작성일: 2026-03-23  
대상 프로젝트: `vms-8ch-webrtc`

## 1. 문서 목적

이 문서는 현장 운영자 또는 인수인계받은 개발자가 시스템을 멈추지 않고 점검, 재기동, 장애 확인, 업데이트할 수 있도록 실무 기준으로 정리한 운영 가이드입니다.

본 문서는 아래 자료를 기준으로 통합 작성했습니다.

- `README.md`
- `deploy/docker-compose.yml`
- `scripts/linux/deploy-stack.sh`
- `install.sh`
- `update.sh`
- `WEBUI_MANUAL_KO_2026-03-10.md`
- `docs/NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`

## 2. 시스템 한 줄 요약

이 시스템은 RTSP 카메라 영상을 받아 WebRTC 라이브 뷰를 제공하고, 이벤트를 판정한 뒤 클립/스냅샷을 생성하여 외부 서버로 전달하는 8채널급 엣지 VMS입니다.

핵심 서비스:

- `vms-api`
- `event-recorder`
- `delivery-worker`
- `mediamtx`
- `dxnn-host-infer`
- `postgres`
- `redis`

## 3. 운영자가 가장 먼저 확인할 것

### 3.1 웹 접속

- 운영 UI: `http://127.0.0.1:8080/`
- API 헬스체크: `http://127.0.0.1:8080/healthz`
- DXNN 헬스체크: `http://127.0.0.1:18081/healthz`

### 3.2 기본 상태 점검 명령

```bash
cd /path/to/vms-8ch-webrtc
./scripts/linux/deploy-stack.sh status
./scripts/linux/deploy-stack.sh logs
./scripts/linux/deploy-stack.sh ctl monitor overview
```

### 3.3 정상 상태 기준

- `vms-api`가 healthy
- `dxnn-host-infer`가 healthy
- `postgres`, `redis`, `mediamtx`, `event-recorder`, `delivery-worker`가 실행 중
- UI 접속 가능
- 최근 이벤트 로그가 갱신됨
- 필요한 카메라가 online 상태

## 4. 주요 운영 절차

### 4.1 최초 설치 후 점검

1. `deploy/.env` 값 확인
2. `./scripts/linux/deploy-stack.sh up` 또는 설치 패키지 기준 `sudo ./install.sh` 실행
3. `./scripts/linux/deploy-stack.sh status` 확인
4. `http://127.0.0.1:8080/healthz` 확인
5. UI 접속 후 카메라 목록, 라이브, 이벤트 화면 확인

### 4.2 일상 점검

하루 운영 점검 시 권장 순서:

1. `status` 로 컨테이너 상태 확인
2. `ctl monitor overview` 로 요약 상태 확인
3. UI에서 최근 이벤트 갱신 여부 확인
4. 카메라 상태 카드에서 offline 장비 확인
5. 목적지 서버 전송 실패가 없는지 확인

### 4.3 재시작

```bash
cd /path/to/vms-8ch-webrtc
VMS_SKIP_BUILD=true ./scripts/linux/deploy-stack.sh restart
```

주의:

- 일반 운영 환경에서는 `VMS_SKIP_BUILD=true` 를 사용하는 것이 안전합니다.
- 실서버에서 불필요한 로컬 재빌드는 피합니다.

### 4.4 완전 종료

```bash
./scripts/linux/deploy-stack.sh down
```

### 4.5 로그 확인

```bash
./scripts/linux/deploy-stack.sh logs
./scripts/linux/deploy-stack.sh logs vms-api
./scripts/linux/deploy-stack.sh logs vms-event-recorder
./scripts/linux/deploy-stack.sh logs vms-delivery-worker
```

우선순위:

- UI/API 문제: `vms-api`
- 이벤트 미발생: `vms-event-recorder`
- 외부 전송 실패: `vms-delivery-worker`
- 라이브 문제: `mediamtx`
- 추론 문제: `vms-dxnn-host-infer`

## 5. UI 기준 운영 절차

### 5.1 새 카메라 추가

1. 카메라 수동 등록 또는 자동 검색
2. 라이브 화면 확인
3. ROI 설정
4. 이벤트 정책 설정
5. 필요 시 이벤트 팩/AI 모델 설정
6. 수동 이벤트 또는 AI 디버그로 검증
7. 목적지 및 라우팅 설정

### 5.2 운영 중 꼭 같이 봐야 하는 화면

- 이벤트 로그
- 카메라 상태 카드
- 라이브 화면
- ROI/정책 화면
- 목적지/라우팅 화면

상세 설명은 [`../WEBUI_MANUAL_KO_2026-03-10.md`](../WEBUI_MANUAL_KO_2026-03-10.md)를 기준으로 봅니다.

## 6. 자주 발생하는 장애와 1차 대응

### 6.1 UI는 열리는데 영상이 안 보임

점검 순서:

1. 카메라 RTSP URL 확인
2. `webrtcPath` 중복/오타 확인
3. `mediamtx` 실행 상태 확인
4. `8889` 포트 응답 여부 확인
5. 브라우저에서 WebRTC 접속 제한 여부 확인

### 6.2 이벤트가 안 생김

점검 순서:

1. 카메라가 online 상태인지 확인
2. 라이브 화면에서 영상이 나오는지 확인
3. ROI가 과도하게 좁거나 비활성화되지 않았는지 확인
4. 이벤트 정책이 활성 상태인지 확인
5. 이벤트 팩과 AI 설정이 올바른지 확인
6. `event-recorder` 로그 확인

### 6.3 스냅샷/클립은 생기는데 전송이 안 됨

점검 순서:

1. 목적지 URL 확인
2. `terminalId`, `cctvId`, 카메라별 매핑 확인
3. 인증 토큰 환경변수 확인
4. 라우팅 규칙 연결 여부 확인
5. `delivery-worker` 로그 확인

### 6.4 DXRT / 추론이 동작하지 않음

점검 순서:

1. `http://127.0.0.1:18081/healthz` 확인
2. `DXRT_HOST_DIR`, `DXRT_HOST_LIB_DIR` 경로 확인
3. 호스트에 `/dev/dxrt0` 존재 여부 확인
4. `dxrt.service` 활성 여부 확인
5. 필요 시 [`NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`](./NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md) 참고

## 7. 운영 명령 모음

### 7.1 상태/제어

```bash
./scripts/linux/deploy-stack.sh status
./scripts/linux/deploy-stack.sh restart
./scripts/linux/deploy-stack.sh down
./scripts/linux/deploy-stack.sh ctl --help
```

### 7.2 모니터링

```bash
./scripts/linux/deploy-stack.sh ctl monitor overview
./scripts/linux/deploy-stack.sh ctl camera list
./scripts/linux/deploy-stack.sh ctl destination check
```

### 7.3 설치/호스트 준비

```bash
sudo ./scripts/linux/prepare-host.sh
sudo ./install.sh
```

### 7.4 업데이트

```bash
./update.sh --source <rsync-source>
```

## 8. 중요한 파일 위치

- 환경 파일: `deploy/.env`
- 런타임 설정: `config/vms.example.yaml`
- Compose 파일: `deploy/docker-compose.yml`
- API/UI 코드: `services/api/`
- 이벤트 워커: `services/recorder/`
- 전송 워커: `services/delivery/`
- 런타임 데이터: `runtime/media`, `runtime/redis`
- DB 스키마: `db/schema.sql`

## 9. 백업/보존 관점

실운영 관점에서 우선 백업해야 하는 대상:

- `deploy/.env`
- 운영용 `vms.yaml` 또는 설정 파일
- 필요 시 `runtime/media`
- 필요 시 DB 데이터

주의:

- `runtime/media` 는 용량 증가가 빠를 수 있으므로 장기 보관 정책을 분리해서 가져가는 편이 좋습니다.
- Compose 기본 설정상 PostgreSQL은 named volume `vms_pg_data` 를 사용합니다.

## 10. 운영 시 주의사항

- 실서버에서는 기본적으로 로컬 재빌드를 하지 않습니다.
- 모델 파일과 DXRT 라이브러리 경로는 배포 전 반드시 확인합니다.
- 카메라 추가 후에는 무조건 라이브, ROI, 이벤트, 전송까지 한 번에 검증합니다.
- 이벤트만 확인하지 말고 실제 아티팩트 생성과 외부 전송 완료까지 확인합니다.
- README, UI 매뉴얼, 장애 대응 문서를 따로 보지 않도록 이 운영 가이드를 기준 문서로 사용합니다.

## 11. 함께 보면 좋은 문서

- [`README.md`](../README.md)
- [`SW_PIPELINE_ARCHITECTURE_KO.md`](./SW_PIPELINE_ARCHITECTURE_KO.md)
- [`../WEBUI_MANUAL_KO_2026-03-10.md`](../WEBUI_MANUAL_KO_2026-03-10.md)
- [`NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`](./NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md)
- [`EDGE_BASIC_EVENT_ALGORITHMS_KO.md`](./EDGE_BASIC_EVENT_ALGORITHMS_KO.md)

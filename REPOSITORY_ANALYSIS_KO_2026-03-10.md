# VMS 8CH WebRTC 저장소 분석서

작성일: 2026-03-10
대상 경로: `/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc`

## 1. 개요

이 저장소는 최대 8대 RTSP 카메라를 대상으로 하는 Edge VMS 프로토타입입니다. Docker Compose를 중심으로 구성되어 있으며 다음 기능을 제공합니다.

- 카메라 등록 및 상태 관리
- MediaMTX 기반 WebRTC 라이브 뷰
- AI 또는 모델 결과 기반 이벤트 생성
- 이벤트 발생 시 스냅샷 또는 클립 생성
- 외부 HTTP/SFTP 목적지로 아티팩트 전송
- FastAPI가 제공하는 Web UI

겉보기에는 스타터 프로젝트처럼 보일 수 있지만, 실제로는 API, Recorder Worker, Delivery Worker, 정적 Web UI, SQLite 개발 모드, 이벤트 팩 로직, DXNN/DXRT 연동 코드까지 포함된 실행 가능한 프로토타입입니다. 다만 운영 하드닝은 아직 부족합니다.

## 2. 디렉터리 구조

- `config/`
  - `vms.example.yaml`: 예시 런타임 설정
  - `event_packs/edge-basic@1.0.0.json`: Recorder가 사용하는 이벤트 팩
- `db/`
  - `schema.sql`: 전체 초기 스키마
  - `migrations/`: 증분 마이그레이션
  - `erd.md`: 간단한 ERD 요약
- `deploy/`
  - `.env`, `.env.example`
  - `docker-compose.yml`: 운영형 Compose
  - `docker-compose.dev.yml`: 개발형 Compose
  - `mediamtx.yml`: MediaMTX 설정
- `models/`
  - `yolo_person_exit_model.py`: YOLO 기반 모델 실행기
  - `dxnn_helmet_runner.py`: DXNN 기반 헬멧 감지 실행기
  - 샘플/테스트용 모델 실행 스크립트
  - 모델 가중치 파일
- `openapi/`
  - `vms-api.yaml`: API 초안 문서
- `runtime/`
  - 미디어/Redis 등 런타임 데이터 루트
- `scripts/`
  - `linux/`: DXRT/DXNN 호스트 설치 및 서비스 스크립트
  - `windows/`: 개발/테스트/배포 보조 스크립트
- `services/`
  - `api/`: FastAPI 백엔드와 정적 UI
  - `recorder/`: 카메라 감시, 추론, 이벤트, 아티팩트 생성
  - `delivery/`: 전송 워커

## 3. 전체 아키텍처

`deploy/docker-compose.yml` 기준 서비스는 6개입니다.

- `postgres`
  - 메타데이터 저장
  - `db/schema.sql`로 초기화
- `redis`
  - 스택에는 포함되어 있으나 현재 핵심 로직 의존성은 낮음
- `mediamtx`
  - RTSP ingest 및 WebRTC 제공
- `vms-api`
  - REST API + Web UI
- `event-recorder`
  - RTSP 연결 확인
  - 모델 실행
  - 이벤트 생성
  - 클립/스냅샷 생성
- `delivery-worker`
  - 아티팩트 재시도 기반 전송

실제 데이터 흐름은 다음과 같습니다.

1. 카메라가 `cameras` 테이블에 등록됩니다.
2. Recorder가 RTSP 연결 상태를 점검하고 `cameras.status`, `recorder_camera_health`를 갱신합니다.
3. 카메라별 모델 설정이 켜져 있으면 모델 스크립트를 실행합니다.
4. 모델 결과는 `ai_detection_logs`에 기록됩니다.
5. Recorder는 다음 세 경로 중 하나로 이벤트를 생성합니다.
   - 이벤트 팩 규칙 평가
   - 모델이 직접 반환한 `events`
   - 단일 `trigger` 기반 하위 호환 이벤트
6. `events`가 생성되면 스냅샷 또는 클립이 생성되어 `artifacts`에 저장됩니다.
7. `routing_rules`에 따라 `delivery_attempts`가 생성됩니다.
8. Delivery Worker가 외부 목적지로 전송하고 상태를 갱신합니다.

## 4. 설정 체계

### 4.1 YAML 설정

`config/vms.example.yaml`에는 다음과 같은 제품 수준 설정이 담겨 있습니다.

- 서버 이름, 타임존
- 최대 카메라 수
- WebRTC/ingest 옵션
- 저장소 경로 및 보존일
- 예시 이벤트 정책
- 전송 목적지와 라우팅

다만 현재 Python 서비스 구현은 이 YAML을 핵심 실행 설정으로 깊게 사용하지 않습니다. 실제 동작의 주된 기준은 아래 세 가지입니다.

- Compose 환경변수
- PostgreSQL 설정값
- API로 저장되는 카메라별 설정

즉, YAML은 현재 "의도된 설정 인터페이스"에 가깝고, 런타임의 단일 진실원천은 아닙니다.

### 4.2 `.env`

`deploy/.env` 및 `.env.example`에는 현재 다음 항목이 있습니다.

- `VMS_DATA_ROOT`
- `DXRT_HOST_DIR`
- `DXRT_HOST_LIB_DIR`

이 값들은 주로 Compose 볼륨 마운트 경로를 제어합니다.

### 4.3 운영 기본값

`deploy/docker-compose.yml`에서 눈에 띄는 기본값은 다음과 같습니다.

- `AUTH_ENABLED=false`
- `JWT_SECRET=change-me`
- 기본 사용자
  - `admin/admin`
  - `operator/operator`
- `DXNN_HOST_INFER_URL=http://host.docker.internal:18081/infer`
- `DXNN_HOST_REQUIRED=true`
- `USE_FFMPEG_ARTIFACTS=true`
- `ENABLE_RTSP_RING_BUFFER=false`

운영 관점에서는 인증, 시크릿, AI 호스트 의존성, ring buffer 사용 여부를 먼저 검토해야 합니다.

## 5. 데이터베이스 구조

핵심 테이블은 다음과 같습니다.

- `cameras`
  - 카메라 메타데이터와 RTSP/WebRTC 경로
- `event_policies`
  - 카메라별 이벤트별 저장 방식
- `camera_rois`
  - 카메라별 ROI 영역
- `app_settings`
  - 전역 설정
- `camera_model_settings`
  - 카메라별 모델 경로/임계값/옵션
- `camera_event_pack_settings`
  - 카메라별 이벤트 팩 설정
- `events`
  - 감지된 이벤트
- `artifacts`
  - 이벤트로 생성된 파일
- `destinations`
  - 외부 전송 대상
- `routing_rules`
  - 이벤트/카메라와 목적지 연결
- `delivery_attempts`
  - 전송 재시도 상태
- `ai_detection_logs`
  - 추론 로그
- `ai_camera_state`
  - 쿨다운 상태
- `recorder_camera_health`
  - RTSP 연결/링버퍼 상태

마이그레이션 흐름은 다음과 같습니다.

- `0001_init.sql`
  - 기본 스키마
- `0002_recorder_camera_health.sql`
  - Recorder health 추가
- `0003_edge_event_pack.sql`
  - 카메라별 모델 설정
  - 이벤트 팩 설정
  - `webrtc`, `person_event_rule` 추가

## 6. API 서비스 분석

구현 파일은 `services/api/app/main.py`입니다.

### 6.1 기술 스택

- FastAPI
- psycopg 3
- 정적 HTML/CSS/JS 직접 서빙
- JWT 인증

### 6.2 주요 API 기능

구현상 포함된 주요 기능:

- 인증
  - `/auth/login`
  - `/auth/me`
  - `/auth/hash-password`
- 상태 점검
  - `/healthz`
- 카메라 관리
  - 목록/생성/수정/삭제
  - `/cameras/discover`
  - `/cameras/discover/jobs`
  - `/cameras/{id}/snapshot`
- ROI 설정
- 이벤트 정책 설정
- 전역 설정
  - AI 모델 설정
  - person event rule
  - WebRTC 설정
- 카메라별 모델 설정
- 카메라별 이벤트 팩 설정
- 모델 목록 조회
- AI preview/debug
- 이벤트 팩 조회
- 목적지/라우팅 관리
- 이벤트/아티팩트 조회
- 재전송
- 모니터링 카메라 상태 조회

### 6.3 카메라 검색

검색은 다음 조합을 지원합니다.

- RTSP 후보 URL 직접 probe
- 선택적 ONVIF 검색

비동기 검색 작업도 존재하지만, 상태 저장은 메모리 딕셔너리(`DISCOVER_JOBS`)에만 있습니다. 따라서 API 재시작 시 검색 작업 상태는 사라집니다.

### 6.4 인증

인증은 환경변수로 켜고 끄는 구조입니다.

- `AUTH_ENABLED=false`이면 권한 검사가 사실상 비활성화
- 활성화되면 JWT 기반 인증
- 사용자 정보는 `AUTH_USERS_JSON`에서 읽음

테스트 환경에는 편리하지만 운영 환경에는 그대로 두면 위험합니다.

## 7. Web UI 분석

정적 UI 파일은 `services/api/app/static/`에 있습니다.

확인된 주요 화면:

- `index.html`
- `page-camera.html`
- `page-live.html`
- `page-monitor.html`
- `page-roi.html`
- `page-policy.html`
- `page-route.html`
- `page-ai.html`
- `page-ai-debug.html`
- `page-discover.html`
- `page-event.html`
- `network-settings.html`
- `camera-settings.html`

`app.js`, `camera-settings.js`, `network-settings.js`를 보면 단순 샘플 페이지가 아니라 실제 운영 흐름을 상당 부분 지원합니다.

지원되는 대표 UI 기능:

- 카메라 등록/수정/삭제
- 카메라 검색 후 일괄 등록
- ROI 편집
  - 사각형
  - 다각형
  - 스냅샷 배경 사용
- 이벤트 정책 설정
- 카메라별 모델 설정
- 이벤트 팩 선택 및 파라미터 입력
- 목적지 생성 및 라우팅 설정
- 이벤트 목록/클리어
- 수동 이벤트 생성
- 라이브 뷰 base URL 설정
- 인증 로그인
- 대시보드 및 모니터링

## 8. Recorder Worker 분석

구현 파일은 `services/recorder/worker.py`입니다.

이 서비스가 실제 VMS 파이프라인의 핵심입니다.

### 8.1 역할

- 미디어 디렉터리 생성
- 카메라 RTSP 연결 확인
- 카메라 상태 업데이트
- 링 레코더 유지
- 모델 스크립트 실행
- 추론 로그 저장
- 이벤트 생성
- 아티팩트 생성
- 전송 큐 생성
- 디스크 부족 시 로그/이벤트 정리

### 8.2 카메라 연결 상태

RTSP 연결 검사는 실제 미디어 디코딩이 아니라 TCP 연결 후 RTSP `OPTIONS` 요청을 보내는 방식입니다. 실패 시 카메라별로 재시도 backoff를 둡니다.

현재 쿼리 상 활성 카메라는 최대 8대까지만 대상으로 처리합니다.

### 8.3 모델 실행 방식

Recorder는 모델을 내부 함수로 직접 호출하지 않고 외부 Python 스크립트를 subprocess로 실행합니다.

- 모델 경로가 `.py`이면 스크립트 직접 실행
- `.dxnn`이면 `dxnn_helmet_runner.py`
- 그 외 일반 모델은 `yolo_person_exit_model.py` 사용

stdin으로 JSON 요청을 보내고 stdout의 JSON 응답을 파싱하는 구조입니다.

장점:

- 모델 교체가 쉬움
- Recorder와 모델 로직 분리

단점:

- 프로세스 실행 오버헤드
- 표준출력 파싱 실패 가능성
- 모델 종속성 관리가 까다로움

### 8.4 이벤트 생성 경로

이벤트는 세 층위에서 생성됩니다.

1. 이벤트 팩 규칙
2. 모델이 직접 반환한 `events`
3. 단일 `trigger` fallback

이 구조 덕분에 단순 모델과 복합 모델을 모두 수용할 수 있습니다.

### 8.5 이벤트 팩

현재 이벤트 팩 파일은 `config/event_packs/edge-basic@1.0.0.json`입니다.

구현된 주요 규칙:

- `person_cross_roi`
- `helmet_missing_in_roi`
- `vehicle_move_without_signalman`
- `no_parking_stop`

Recorder 내부에는 다음 로직이 구현돼 있습니다.

- 사각형/다각형 ROI 판정
- 사람 중심점/바운딩박스 기준 진입 판정
- ROI 중첩 비율 계산
- 차량 정지 추적
- 규칙별 cooldown 처리

즉, 이벤트 팩은 선언형 JSON이지만 실질 의미는 Recorder 코드가 구현합니다.

### 8.6 아티팩트 생성

이벤트에 아티팩트가 없으면 Recorder가 생성합니다.

- 기본은 `snapshot`
- 정책이 `clip`이면 클립 생성

생성 방식:

- ffmpeg로 실제 파일 생성
- 실패하면 placeholder 파일 생성

중요한 제한:

- ring buffer가 꺼져 있으면 진짜 pre-event clip은 구현되지 않음
- 코드 주석에도 단순 모드에서는 pre-event clip 미구현이라고 명시돼 있음

### 8.7 디스크 관리

디스크 여유가 부족하면 다음 항목이 삭제될 수 있습니다.

- 오래된 `ai_detection_logs`
- 전송이 걸려 있지 않은 오래된 `events`

간단한 보호장치로는 유용하지만 세밀한 보존정책은 아닙니다.

## 9. Delivery Worker 분석

구현 파일은 `services/delivery/worker.py`입니다.

### 9.1 역할

- 전송 대기 건 조회
- 행 잠금
- 전송 수행
- 성공/실패 상태 반영
- 재시도 스케줄링
- 성공 후 로컬 파일 삭제

### 9.2 전송 방식

코드상 지원:

- `https_post`
- `sftp`

하지만 API는 목적지 생성 시 현재 `https_post`만 허용합니다. 즉, 스키마/Worker/API 사이에 차이가 있습니다.

### 9.3 HTTP 전송

현재 HTTP 전송은 일반 webhook이 아니라 특정 규격을 강하게 가정합니다.

- `apiMode = cctv_img_v1`
- snapshot 전용
- multipart 업로드
- event type을 숫자 코드로 변환
- `terminalId`, `cctvId` 필요

즉, 범용 전송기가 아니라 특정 수신 시스템 맞춤 연동입니다.

### 9.4 재시도

재시도 backoff:

- 5초
- 15초
- 30초
- 60초
- 120초

전송 보장은 at-least-once 성격입니다.

## 10. 모델 분석

### 10.1 `yolo_person_exit_model.py`

이름과 달리 현재 기본 동작은 "사람 출현 지속시간" 기반입니다.

- `personEventRule.enabled = true`
  - 사람이 일정 시간 이상 보이면 이벤트
- `false`
  - 사람이 사라진 시간 기준 exit 이벤트

즉, 실제 기본 이벤트는 "person dwell" 쪽에 가깝습니다.

### 10.2 `dxnn_helmet_runner.py`

두 단계 동작을 합니다.

1. 호스트 추론 서비스가 있으면 먼저 호출
2. 없거나 필수가 아니면 로컬 DXNN 추론

주요 기능:

- 모델 메타 읽기
- 입력 shape 유추
- RTSP에서 프레임 1장 획득
- 전처리
- YOLO 유사 출력 해석
- person/head/helmet 조합으로 헬멧 미착용 판단

범용 DXNN 실행기라기보다는 헬멧 감지용 특화 러너입니다.

## 11. DXRT / DXNN 호스트 연동 상태

관련 스크립트:

- `scripts/linux/install_dxrt_host.sh`
- `scripts/linux/install_dxnn_host_service.sh`
- `scripts/linux/dxnn_host_infer_service.py`

체크인된 핸드오프 문서 기준 현재 상태:

- 대상 서버 `192.168.1.165`에 Compose 스택은 올라가 있음
- API 헬스체크는 정상
- DXRT 설치 중 `ONNXLIB_DIRS` 문제 발생
- `dxrt.service` 미구성으로 DXNN host infer 서비스도 완전 기동 실패

정리하면:

- VMS 기본 기능은 가능
- DXNN 호스트 추론 기반 AI 기능은 아직 미완성

## 12. 개발 모드

`deploy/docker-compose.dev.yml` 기준 개발 모드는:

- `vms-api-dev`
- `vms-worker-dev`

구성이며 SQLite를 사용합니다.

개발 모드 특징:

- 로컬 UI/API 수정에 적합
- 운영 모드보다 단순
- PostgreSQL/Delivery/Auth/Event pack 세부 구현 차이가 존재

따라서 기능 검증은 가능하지만 운영 등가 환경은 아닙니다.

## 13. OpenAPI와 실제 구현 차이

`openapi/vms-api.yaml`은 초안 수준입니다.

실제 코드와 비교하면:

- 구현 엔드포인트가 더 많음
- 인증/설정/디버그/모니터링 기능이 더 풍부함
- 목적지 처리 방식이 문서보다 더 제한적임

따라서 실제 연동 기준은 OpenAPI 초안보다 실행 중인 FastAPI 코드입니다.

## 14. 현재 운영 상태

`HANDOFF_2026-03-10_165.md` 기준:

- 배포 위치: `192.168.1.165`
- API health: 정상
- 서비스 6개 가동 확인
- 미디어/Redis 데이터는 `runtime/` 경로 사용
- PostgreSQL은 bind mount 대신 Docker named volume 사용

그 이유는 대상 디스크의 권한/파일시스템 특성상 공식 Postgres 이미지가 요구하는 권한 변경을 만족하지 못했기 때문입니다.

## 15. 장점

- 구조가 단순하고 이해하기 쉬움
- API / Recorder / Delivery 역할 분리 명확
- 이벤트 팩 로직이 비교적 잘 구현됨
- ROI, 모델 설정, 라우팅, 수동 이벤트까지 UI가 실제 사용 가능 수준
- ffmpeg/placeholder fallback이 있어 장애 허용성이 있음
- DB 기반 delivery lock 처리 방식이 안정적임

## 16. 약점 및 리스크

### 16.1 보안

- 인증 비활성 기본값
- 약한 JWT secret
- 기본 계정 존재
- `.env` 체크인

### 16.2 설정 분산

설정 기준이 여러 곳에 나뉘어 있습니다.

- YAML
- 환경변수
- DB
- 카메라별 설정
- 모델별 env

운영 중 장애 분석이 어려워질 수 있습니다.

### 16.3 Redis 활용도

Redis가 배포되지만 핵심 업무 로직의 실질 의존도는 낮습니다. 향후 사용 계획이 없다면 단순화 대상입니다.

### 16.4 메모리 상태 의존

- 검색 작업 상태
- 일부 runtime state

가 프로세스 메모리에만 있어 재시작 시 소실됩니다.

### 16.5 스펙 불일치

- OpenAPI와 실제 구현 차이
- API는 `https_post`만 허용하지만 Worker는 SFTP 지원
- YAML과 실제 런타임 동작 차이

### 16.6 클립 의미 차이

ring buffer가 꺼진 상태에서는 "클립"이 운영자가 기대하는 진짜 pre/post event clip과 다를 수 있습니다.

### 16.7 DXRT 의존성

AI 런타임, 특히 DXNN host path는 환경 의존성이 높고 아직 배포 안정화가 덜 됐습니다.

## 17. 권장 조치

### 단기

1. 운영 배포에서 인증 활성화
2. `JWT_SECRET` 교체
3. 기본 계정 제거 또는 해시 기반 사용자로 교체
4. 목적지 API와 Worker 지원 범위 정렬
5. 어떤 설정이 최종 기준인지 문서화
6. 실제 카메라로 end-to-end 테스트 수행
7. pre-event clip이 필요하면 ring buffer 설계 확정

### 중기

1. OpenAPI 문서 최신화
2. 검색 작업 상태를 영속화 또는 큐 기반으로 변경
3. 로그/메트릭/장애 추적 강화
4. 모델 실행기 구조 정리
5. 특정 수신 시스템 종속 HTTP 전송을 일반화하거나 분리

## 18. 결론

이 저장소는 "카메라 등록 -> 추론 -> 이벤트 생성 -> 아티팩트 생성 -> 외부 전송" 흐름이 실제로 동작하는 VMS 프로토타입입니다. 가장 강한 부분은 Recorder와 이벤트 팩 로직이며, 가장 취약한 부분은 운영 보안, 설정 일관성, DXRT/DXNN 연동 안정성입니다.

파일럿 또는 실험 환경에는 충분히 활용 가능하지만, 운영 환경 투입 전에는 인증/시크릿/설정체계/API 문서/AI 런타임 안정화 작업이 필요합니다.

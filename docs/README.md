# VMS 문서 인덱스

이 디렉터리는 `vms-8ch-webrtc` 프로젝트의 설치, 운영, 아키텍처, 이벤트 알고리즘 관련 문서를 모아 둔 공간입니다.

문서를 처음 보는 경우에는 아래 순서로 읽는 것이 가장 효율적입니다.

1. [`../README.md`](../README.md)
2. [`OPERATIONS_GUIDE_KO.md`](./OPERATIONS_GUIDE_KO.md)
3. [`SW_PIPELINE_ARCHITECTURE_KO.md`](./SW_PIPELINE_ARCHITECTURE_KO.md)
4. [`../WEBUI_MANUAL_KO_2026-03-10.md`](../WEBUI_MANUAL_KO_2026-03-10.md)

## 문서 목록

### 프로젝트/운영 기준 문서

- [`OPERATIONS_GUIDE_KO.md`](./OPERATIONS_GUIDE_KO.md)
  일상 운영 절차, 장애 대응, 점검 명령, 백업/업데이트 기준을 정리한 운영 가이드
- [`../README.md`](../README.md)
  프로젝트 개요, 구성요소, 빠른 시작, 설치 스크립트 요약
- [`SW_PIPELINE_ARCHITECTURE_KO.md`](./SW_PIPELINE_ARCHITECTURE_KO.md)
  서비스 간 연결 구조와 데이터 흐름 설명
- [`../WEBUI_MANUAL_KO_2026-03-10.md`](../WEBUI_MANUAL_KO_2026-03-10.md)
  운영 UI 화면별 사용 가이드

### 설치/배포/장애 대응 문서

- [`NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`](./NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md)
  NAS 배포와 DXRT 설치 이슈 해결 이력
- [`NAS_SESSION_START_2026-03-19.md`](./NAS_SESSION_START_2026-03-19.md)
  당시 작업 세션의 시작 메모와 현장 맥락
- [`MODEL_EXPORT_2026-03-20_STATUS.md`](./MODEL_EXPORT_2026-03-20_STATUS.md)
  모델 export 상태와 관련 메모

### 이벤트 알고리즘 문서

- [`EDGE_BASIC_EVENT_ALGORITHMS_KO.md`](./EDGE_BASIC_EVENT_ALGORITHMS_KO.md)
  엣지 기본 이벤트 팩 알고리즘 설명
- [`edge-basic-diagrams/`](./edge-basic-diagrams)
  Mermaid 원본 및 생성 이미지

### 시각 자료

- [`webui-screenshots/`](./webui-screenshots)
  UI 설명용 이미지
- [`sw-pipeline/`](./sw-pipeline)
  파이프라인 Mermaid 원본 및 렌더 결과

## 어떤 문서를 언제 보는가

- 시스템 처음 인수인계받는 경우
  [`../README.md`](../README.md), [`OPERATIONS_GUIDE_KO.md`](./OPERATIONS_GUIDE_KO.md), [`SW_PIPELINE_ARCHITECTURE_KO.md`](./SW_PIPELINE_ARCHITECTURE_KO.md)
- 운영자가 UI 사용법을 확인하는 경우
  [`../WEBUI_MANUAL_KO_2026-03-10.md`](../WEBUI_MANUAL_KO_2026-03-10.md)
- DXRT나 NAS 설치 문제를 추적하는 경우
  [`NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`](./NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md)
- 이벤트 판정 로직을 검토하는 경우
  [`EDGE_BASIC_EVENT_ALGORITHMS_KO.md`](./EDGE_BASIC_EVENT_ALGORITHMS_KO.md)

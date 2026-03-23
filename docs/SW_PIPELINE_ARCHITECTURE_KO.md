# HANJIN CCTV SW 파이프라인 구성도

이 문서는 현재 `vms-8ch-webrtc` 저장소 기준 소프트웨어 파이프라인 구성을 한 장으로 정리한 것입니다.

기준 파일:

- `README.md`
- `deploy/docker-compose.yml`
- `services/api`
- `services/recorder`
- `services/delivery`

## 파이프라인 구성도

```mermaid
flowchart LR
    classDef ext fill:#f8fafc,stroke:#94a3b8,color:#0f172a,stroke-width:1px;
    classDef svc fill:#eef2ff,stroke:#6366f1,color:#1e1b4b,stroke-width:1px;
    classDef store fill:#ecfeff,stroke:#0f766e,color:#134e4a,stroke-width:1px;
    classDef worker fill:#fff7ed,stroke:#ea580c,color:#7c2d12,stroke-width:1px;

    User[Operator Browser<br/>Web UI / WebRTC Viewer]:::ext
    Camera[IP Camera / RTSP Source]:::ext
    Dest[External Receiver / NAS / HTTP Destination]:::ext

    subgraph Stack[HANJIN CCTV SW Pipeline]
        API[vms-api<br/>REST API + Static GUI]:::svc
        OPS[vms-ops<br/>CLI / Operations]:::svc
        MTX[mediamtx<br/>RTSP ingest + WebRTC endpoint]:::svc
        REC[event-recorder<br/>camera polling + event evaluation<br/>clip/snapshot generation]:::worker
        DXNN[dxnn-host-infer<br/>DXNN / DXRT inference bridge]:::worker
        DEL[delivery-worker<br/>artifact forwarding]:::worker
        PG[(postgres<br/>metadata / events / policies)]:::store
        RDS[(redis<br/>queue / cache / state helper)]:::store
        MEDIA[(runtime media root<br/>clips / snapshots / artifacts)]:::store
        MODEL[(models<br/>DXNN / Python model scripts)]:::store
    end

    User -->|HTTP 8080| API
    OPS -->|API control| API
    User -->|WebRTC live view| MTX

    API -->|camera / ROI / policy CRUD| PG
    API -->|runtime helper| RDS
    API -->|health / infer request| DXNN
    API -->|stream config / playback info| MTX

    Camera -->|RTSP ingest| MTX
    MTX -->|RTSP stream| REC

    REC -->|load event pack / camera policy| PG
    REC -->|dedup / state helper| RDS
    REC -->|infer request| DXNN
    DXNN -->|model files| MODEL
    DXNN -->|detections / trigger result| REC

    REC -->|event create / artifact metadata| PG
    REC -->|clip / snapshot write| MEDIA

    DEL -->|pending event / route lookup| PG
    DEL -->|artifact read| MEDIA
    DEL -->|delivery result update| PG
    DEL -->|HTTP upload / transfer| Dest

    API -.monitoring / list.-> PG
    API -.artifact browse.-> MEDIA
```

## 흐름 요약

1. 운영자는 `vms-api`를 통해 카메라, ROI, 이벤트 정책, 전송 대상을 관리합니다.
2. 카메라 영상은 `mediamtx`로 들어오고, 브라우저는 여기서 WebRTC 라이브 뷰를 받습니다.
3. `event-recorder`는 스트림을 읽고 `dxnn-host-infer`에 추론을 요청합니다.
4. 추론 결과와 이벤트 팩 규칙을 바탕으로 Recorder가 최종 이벤트를 판정합니다.
5. 이벤트가 발생하면 Recorder가 클립/스냅샷을 `runtime media root`에 저장하고 메타데이터를 `postgres`에 기록합니다.
6. `delivery-worker`는 적재된 이벤트와 아티팩트를 읽어 외부 수신처로 전송합니다.

## 저장 위치

- Mermaid 원본: `docs/sw-pipeline/hanjin-sw-pipeline.mmd`
- 설명 문서: `docs/SW_PIPELINE_ARCHITECTURE_KO.md`

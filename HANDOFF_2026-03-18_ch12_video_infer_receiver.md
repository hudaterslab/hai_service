# Handoff - 2026-03-18 ch12 video infer -> receiver

## Goal
- Test `video-infer-send` without a live camera.
- Use remote host `192.168.1.38` (`recomputer`) with local video file:
  - `/home/recomputer/hanjin_mp4_2026_03_17/2026_03_17_07_06_43_ch12.mp4`
- Send triggered snapshot events to Hanjin Receiver.

## Receiver / Remote Runtime
- Remote host: `192.168.1.38`
- Remote user: `recomputer`
- Remote password used in session: `1234`
- Receiver base URL found in repo docs:
  - `https://tmlsafety.hudaters.net/receiver`
- Receiver upload endpoint actually registered on remote:
  - `https://tmlsafety.hudaters.net/receiver/api/v1/cctv/img`
- Existing remote destination before this session:
  - `hanjin-receiver`
  - `terminalId = 00003`
  - `cctvId = 1`

## What Was Confirmed On Remote
- `http://127.0.0.1:8080/healthz` returns OK.
- `http://127.0.0.1:18081/healthz` returns OK.
- Containers up:
  - `vms-api`
  - `vms-event-recorder`
  - `vms-dxnn-host-infer`
  - `vms-delivery-worker`
  - `vms-postgres`
  - `vms-redis`
  - `vms-mediamtx`
- Video file exists on remote host.
- Video file was staged into `vms-api` container at:
  - `/tmp/2026_03_17_07_06_43_ch12.mp4`

## Remote Objects Created During This Session
- Camera:
  - name: `video-ch12`
  - id: `30328ee2-09d3-4410-8c6d-057cc3d2c7d2`
- Destination:
  - name: `hanjin-receiver-ch12-test`
  - id: `b2a813c5-3c96-4519-af92-5d2233dc856d`
  - `terminalId = 00003`
  - `cctvId = 12`

## Problems Found

### 1. Old remote `dxnn_host_infer_service.py` did not support `videoPath`
- Symptom:
  - `video-infer-send` returned samples with:
    - `status = model-error:missing_rtsp_url`
- Cause:
  - Remote deployed `dxnn_host_infer_service.py` still required `rtspUrl`.
  - Local source already supported `videoPath`, but remote deployed copy did not.
- Action taken:
  - Patched local repo release copy:
    - [dist/vms-edge-current/scripts/linux/dxnn_host_infer_service.py](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/dist/vms-edge-current/scripts/linux/dxnn_host_infer_service.py)
  - Hot-patched remote `vms-dxnn-host-infer` container with the same file.
  - Restarted `vms-dxnn-host-infer`.
- Result:
  - `missing_rtsp_url` error is gone.

### 2. Real DXNN inference still cannot run on remote
- Symptom after patch:
  - `video-infer-send` returns:
    - `status = model-error:Failed to create InferenceEngine for model '/opt...`
- Direct check inside `vms-dxnn-host-infer`:
  - `InferenceEngine("/opt/vms/models/hf/HudatersU_Safety_helmet/safety_helmet_251209.dxnn", io)`
  - fails with:
    - `dxrt service is not running`
- Meaning:
  - This host currently cannot execute the real `.dxnn` model.
  - This is not a `videoPath` bug anymore.
  - It is a DXRT runtime/service availability problem.

### 3. Full-video `video-infer-send` can exceed client timeout
- First full-file run timed out client-side at 120s.
- Short-window run succeeded and returned JSON.
- For next session, continue with bounded windows first:
  - `startOffsetSec`
  - `endOffsetSec`
  - `sampleIntervalSec`

## Local Repo Changes Made
- Added helper script:
  - [scripts/linux/video_infer_receiver_test.py](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/scripts/linux/video_infer_receiver_test.py)
- Updated release copy of remote host infer service:
  - [dist/vms-edge-current/scripts/linux/dxnn_host_infer_service.py](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/dist/vms-edge-current/scripts/linux/dxnn_host_infer_service.py)

## Most Important Next-Session Decision
- Choose one path first:
  1. Fix DXRT service on `192.168.1.38` so real `.dxnn` inference works.
  2. If end-to-end receiver path is the immediate priority, switch the test camera to:
     - `/opt/vms/models/force_trigger_model.py`
     - This should validate:
       - video snapshot extraction
       - event creation
       - artifact save
       - receiver delivery
     - without requiring DXRT.

## Suggested First Commands Next Session
- Reconfirm remote DXRT failure:
```bash
sudo docker exec vms-dxnn-host-infer sh -lc 'python3 - <<\"PY\"\nfrom dx_engine import InferenceEngine, InferenceOption\nio = InferenceOption()\nio.buffer_count = 2\ntry:\n    InferenceEngine(\"/opt/vms/models/hf/HudatersU_Safety_helmet/safety_helmet_251209.dxnn\", io)\n    print(\"ENGINE_OK\")\nexcept Exception as e:\n    print(\"ENGINE_FAIL\", repr(e))\nPY'
```

- If validating delivery path first, switch test camera model to `force_trigger_model.py` and rerun a short window:
```bash
POST /cameras/<video-ch12-force-or-test-camera>/model-settings
{
  "enabled": true,
  "modelPath": "/opt/vms/models/force_trigger_model.py",
  "confidenceThreshold": 0.35,
  "timeoutSec": 10,
  "pollSec": 2,
  "cooldownSec": 5,
  "extra": {}
}
```

## Known Useful IDs
- `video-ch12` camera:
  - `30328ee2-09d3-4410-8c6d-057cc3d2c7d2`
- `hanjin-receiver-ch12-test` destination:
  - `b2a813c5-3c96-4519-af92-5d2233dc856d`

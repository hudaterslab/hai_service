# NAS Edge Install And DXRT Troubleshooting

Date: 2026-03-19
Target package: `vms-edge-installer-20260319.tar.gz`
Target rsync release: `vms-edge-current`
Verified host: `192.168.1.38`

## Summary

As of 2026-03-19, the NAS deployment bundle was refreshed and verified on `192.168.1.38`.

The original DXRT failure was not an `onnxruntime` build issue anymore. That earlier blocker was already fixed. The remaining install failure was caused by the NPU driver stack not being installed before validating `dxrt.service`.

The deployment flow now installs the DEEPX driver stack first from:

- `https://github.com/DEEPX-AI/dx-runtime`
- commit `28aa8d1cc0ae23587493e83ff6586d6d519a951a`

After that change, the target host created `/dev/dxrt0`, started `dxrt.service`, and completed the first-time edge install successfully.

## What Was Fixed

### 1. DXRT driver-first install flow

File:

- `scripts/linux/install_dxrt_host.sh`

Applied behavior:

- if `/dev/dxrt0` is missing and `dxrt_driver` / `dx_dma` modules are absent, install `dx_rt_npu_linux_driver` first
- pin the upstream source to commit `28aa8d1cc0ae23587493e83ff6586d6d519a951a`
- fail with a clear reboot/cold-boot message if the driver is installed but the device is still not visible

### 2. Host library sync guard

File:

- `scripts/linux/dxrt_common.sh`

Applied behavior:

- skip copying `libdxrt.so` when source and destination already resolve to the same file

### 3. `current` symlink status fix

File:

- `scripts/linux/deploy-stack.sh`

Applied behavior:

- resolve script and repo paths with `pwd -P`
- `~/vms-install/current/vms-edge status` now uses the real install directory instead of treating the compose project name as `current`

## Verified Result On 192.168.1.38

Verification time:

- `2026-03-19 17:37:16 KST`

Confirmed state:

- `/dev/dxrt0` exists
- `dxrt_driver` and `dx_dma` kernel modules are loaded
- `dxrt.service` is `active`
- `http://127.0.0.1:8080/healthz` returned `{"ok":true}`
- `http://127.0.0.1:18081/healthz` returned `{"ok": true}`
- running containers:
  - `vms-api`
  - `vms-event-recorder`
  - `vms-delivery-worker`
  - `vms-postgres`
  - `vms-redis`
  - `vms-dxnn-host-infer`
  - `vms-mediamtx`
- active install path:
  - `/home/recomputer/vms-install/vms-edge-installer-20260319`

## Install Method From NAS

### Fresh install

```bash
scp -O -P 21423 dhkim@112.217.187.130:/volume1/Hudaters/HanjinCCTV/edge-install-from-nas.sh .
chmod +x edge-install-from-nas.sh
./edge-install-from-nas.sh install --user dhkim
```

If password auth is needed:

```bash
./edge-install-from-nas.sh install --user dhkim --password '<NAS_PASSWORD>' --force-download
```

### In-place update on an installed edge node

```bash
cd ~/vms-install/current
./vms-edge update --user dhkim --password '<NAS_PASSWORD>'
```

### Basic runtime commands

```bash
cd ~/vms-install/current
./vms-edge status
./vms-edge restart
./vms-edge logs vms-api
```

## Issues Seen During This Work

### Issue 1. `ONNXLIB_DIRS` failure

Previous symptom:

- DXRT source build failed before service startup because `onnxruntime` library paths were not detected correctly

Status on 2026-03-19:

- fixed before this round
- no longer the blocking issue

### Issue 2. `dxrt.service` failed with `Device not found`

Observed symptom:

- `dxrtd` started, but service logs showed `Device not found`

Root cause:

- the install flow built or installed userspace runtime components without installing the DEEPX NPU driver stack first

Resolution:

- install `dx_rt_npu_linux_driver` from upstream `dx-runtime` before DXRT validation

### Issue 3. `vms-edge status` looked empty under `~/vms-install/current`

Observed symptom:

- containers were running in `docker ps`, but `./vms-edge status` showed an empty compose table

Root cause:

- compose project name was derived from the symlink path basename `current`

Resolution:

- resolve the real repo path with `pwd -P` in `deploy-stack.sh`

## Operational Notes

- The deployment scripts now block local image rebuilds by default.
- Use `VMS_SKIP_BUILD=true` for normal restart/up flows on installed edge nodes.
- Use `VMS_ALLOW_LOCAL_BUILD=true` only when an intentional local rebuild is required.
- If the driver installs but `/dev/dxrt0` is still missing, reboot or cold-boot the host and rerun the installer.

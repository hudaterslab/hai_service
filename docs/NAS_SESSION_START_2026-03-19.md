# NAS Session Start

Date: 2026-03-19

## Current State

- NAS latest installer: `vms-edge-installer-20260319.tar.gz`
- NAS rsync release: `vms-edge-current`
- Verified target host: `192.168.1.38`
- Verified install path on target host:
  - `/home/recomputer/vms-install/vms-edge-installer-20260319`
- Verified current symlink on target host:
  - `/home/recomputer/vms-install/current`

## What Was Fixed

- DXRT install now installs the DEEPX NPU driver stack first
- DXRT upstream reference pinned to commit:
  - `28aa8d1cc0ae23587493e83ff6586d6d519a951a`
- `~/vms-install/current/vms-edge status` now works correctly
- accidental local Docker rebuilds remain blocked by default

## Verified Result

- `/dev/dxrt0` exists on `192.168.1.38`
- `dxrt.service` is active
- `http://127.0.0.1:8080/healthz` is OK
- `http://127.0.0.1:18081/healthz` is OK
- `./vms-edge status` is OK

## NAS Files To Start From

- `/volume1/Hudaters/HanjinCCTV/edge-install-from-nas.sh`
- `/volume1/Hudaters/HanjinCCTV/LATEST_INSTALLER`
- `/volume1/Hudaters/HanjinCCTV/vms-edge-installer-20260319.tar.gz`
- `/volume1/Hudaters/HanjinCCTV/vms-edge-current`
- `/volume1/Hudaters/HanjinCCTV/NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md`
- `/volume1/Hudaters/HanjinCCTV/NAS_SESSION_START_2026-03-19.md`

## Start Commands

Fresh install:

```bash
scp -O -P 21423 dhkim@112.217.187.130:/volume1/Hudaters/HanjinCCTV/edge-install-from-nas.sh .
chmod +x edge-install-from-nas.sh
./edge-install-from-nas.sh install --user dhkim --password '<NAS_PASSWORD>' --force-download
```

Update existing install:

```bash
cd ~/vms-install/current
./vms-edge update --user dhkim --password '<NAS_PASSWORD>'
```

Check runtime:

```bash
cd ~/vms-install/current
./vms-edge status
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:18081/healthz
```

## If Problems Reappear

- If `dxrt.service` fails with `Device not found`, confirm the driver stack was installed and `/dev/dxrt0` exists.
- If the driver is installed but `/dev/dxrt0` is still missing, reboot or cold-boot the host and rerun install.
- See `NAS_EDGE_INSTALL_AND_DXRT_TROUBLESHOOTING_2026-03-19.md` for full details.

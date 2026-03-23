# Handoff - 2026-03-18 local cleanup and next-session restart

## Goal
- Resume VMS installer / NAS deployment work safely in the next session.
- Avoid rebuilding the full Docker stack on this local Jetson root disk.
- Preserve the repo state and packaged installer artifacts, but keep local Docker empty.

## What Happened In This Session
- Confirmed this workspace is not a git repo, so recovery had to be done from file timestamps and artifacts.
- Verified recent work was mainly:
  - DXRT / DXNN host install-path updates
  - NAS installer / rsync release packaging
  - docs / Mermaid diagram generation
- Attempted local `./scripts/linux/deploy-stack.sh up` to validate runtime.
- That command triggered fresh local Docker image builds for:
  - `vms-api`
  - `event-recorder`
  - `dxnn-host-infer`
  - `delivery-worker`
- Build failed at `vms-api` pip install with:
  - `OSError: [Errno 28] No space left on device`

## Root Cause
- [`scripts/linux/deploy-stack.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/scripts/linux/deploy-stack.sh) uses:
  - `docker compose up -d --build`
  - unless `VMS_SKIP_BUILD=true`
- That means local runtime validation from this repo tries to build heavy Python / ffmpeg / torch / ultralytics images on `/var/lib/docker`.
- Local root disk is the Jetson system disk, not the large project disk.
- Follow-up change now applied in the repo:
  - `deploy-stack.sh up` and `restart` refuse local `--build` unless:
    - `VMS_SKIP_BUILD=true`, or
    - `VMS_ALLOW_LOCAL_BUILD=true`

## Cleanup Performed
- Removed all VMS local Docker containers.
- Removed all VMS local Docker images.
- Removed Docker build cache.
- Removed leftover PostgreSQL Docker volume.
- Removed remaining `postgres`, `redis`, `mediamtx` images.

## Current Local State
- Docker state after cleanup:
  - images: `0`
  - containers: `0`
  - volumes: `0`
  - build cache: `0`
- Disk state after cleanup:
  - root `/`: `57G` total, `28G` used, `27G` free, `51%`
  - project disk `/media/fishduke/06800C3B800C3429/WorkWithCodex`: `216G` free

## Repo / Artifact State To Keep
- [`deploy/.env`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/deploy/.env) exists and still points `VMS_DATA_ROOT` to repo-local `runtime`.
- NAS installer artifact still present:
  - [`dist/LATEST_INSTALLER`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/dist/LATEST_INSTALLER)
  - current value: `vms-edge-installer-20260317.tar.gz`
- Recent package manifest:
  - [`dist/vms-edge-installer-20260317/INSTALLER_MANIFEST.txt`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/dist/vms-edge-installer-20260317/INSTALLER_MANIFEST.txt)
- Static script syntax checks already passed for:
  - `install.sh`
  - `update.sh`
  - `edge-install-from-nas.sh`
  - `edge-update-from-nas.sh`
  - `scripts/linux/build-installer-package.sh`
  - `scripts/linux/build-rsync-release.sh`

## Important Warning For Next Session
- Do not run local:
  - `./scripts/linux/deploy-stack.sh up`
  - `./scripts/linux/deploy-stack.sh restart`
  - `docker compose up --build`
- Safe local inspection command:
  - `VMS_SKIP_BUILD=true ./scripts/linux/deploy-stack.sh up`
- Do not validate by rebuilding on this Jetson root disk unless you intentionally want local images again.

## Safer Paths Next Session
- Preferred path A:
  - Continue NAS / remote-host deployment workflow only.
  - Use [`edge-install-from-nas.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/edge-install-from-nas.sh) on the target host.
- Preferred path B:
  - If local inspection is needed, use file-level review only.
  - Avoid Docker build steps.
- Only if you truly need local container startup:
  - use prebuilt bundled images via installer path, not fresh local rebuild
  - or move Docker root/storage strategy first

## Most Relevant Files To Re-open First
- [`scripts/linux/deploy-stack.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/scripts/linux/deploy-stack.sh)
- [`install.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/install.sh)
- [`edge-install-from-nas.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/edge-install-from-nas.sh)
- [`scripts/linux/build-installer-package.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/scripts/linux/build-installer-package.sh)
- [`scripts/linux/build-rsync-release.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/scripts/linux/build-rsync-release.sh)

## Recommended First Prompt Next Session
- "Read [HANDOFF_2026-03-18_local_cleanup_and_restart.md](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/HANDOFF_2026-03-18_local_cleanup_and_restart.md) and continue from the NAS deployment path only. Do not run local Docker rebuilds."

## Optional Follow-up Improvement
- Guard has now been added to [`scripts/linux/deploy-stack.sh`](/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc/scripts/linux/deploy-stack.sh):
  - `up` and `restart` fail fast unless:
    - `VMS_SKIP_BUILD=true`, or
    - `VMS_ALLOW_LOCAL_BUILD=true`

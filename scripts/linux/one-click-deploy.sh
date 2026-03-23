#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_USER="${SUDO_USER:-root}"
REPORT_DIR="$REPO_ROOT/output/deploy-reports"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="$REPORT_DIR/deploy-$TIMESTAMP.log"
DEPLOY_ENV_VARS=()

usage() {
  cat <<'EOF'
Usage:
  one-click-deploy.sh
  one-click-deploy.sh --skip-docker
  one-click-deploy.sh --skip-dxrt
  one-click-deploy.sh --skip-up

This script:
  1. prepares the host (Docker + DXRT)
  2. initializes deploy/.env and runtime dirs
  3. starts the Docker stack
  4. shows stack status and monitor overview
EOF
}

mkdir -p "$REPORT_DIR"
exec > >(tee -a "$REPORT_FILE") 2>&1

SKIP_DOCKER=false
SKIP_DXRT=false
SKIP_UP=false

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --skip-docker)
      SKIP_DOCKER=true
      shift
      ;;
    --skip-dxrt)
      SKIP_DXRT=true
      shift
      ;;
    --skip-up)
      SKIP_UP=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "${VMS_COMPOSE_PROJECT_NAME:-}" ]] && DEPLOY_ENV_VARS+=("VMS_COMPOSE_PROJECT_NAME=$VMS_COMPOSE_PROJECT_NAME")
[[ -n "${VMS_SKIP_BUILD:-}" ]] && DEPLOY_ENV_VARS+=("VMS_SKIP_BUILD=$VMS_SKIP_BUILD")

prepare_args=()
[[ "$SKIP_DOCKER" == "true" ]] && prepare_args+=(--skip-docker)
[[ "$SKIP_DXRT" == "true" ]] && prepare_args+=(--skip-dxrt)

"$SCRIPT_DIR/prepare-host.sh" "${prepare_args[@]}"

if [[ "$SKIP_UP" == "true" ]]; then
  exit 0
fi

if [[ "$RUN_USER" == "root" ]]; then
  env "${DEPLOY_ENV_VARS[@]}" "$SCRIPT_DIR/deploy-stack.sh" up
  env "${DEPLOY_ENV_VARS[@]}" "$SCRIPT_DIR/deploy-stack.sh" status
  env "${DEPLOY_ENV_VARS[@]}" "$SCRIPT_DIR/deploy-stack.sh" ctl monitor overview || true
else
  sudo -u "$RUN_USER" env "${DEPLOY_ENV_VARS[@]}" "$SCRIPT_DIR/deploy-stack.sh" up
  sudo -u "$RUN_USER" env "${DEPLOY_ENV_VARS[@]}" "$SCRIPT_DIR/deploy-stack.sh" status
  sudo -u "$RUN_USER" env "${DEPLOY_ENV_VARS[@]}" "$SCRIPT_DIR/deploy-stack.sh" ctl monitor overview || true
fi

echo ""
echo "Deploy report saved to: $REPORT_FILE"

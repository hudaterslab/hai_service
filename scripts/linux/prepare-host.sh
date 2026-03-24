#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DOCKER="${INSTALL_DOCKER:-true}"
INSTALL_DXRT="${INSTALL_DXRT:-true}"
DXRT_INSTALL_MODE="${DXRT_INSTALL_MODE:-bundle}"

usage() {
  cat <<'EOF'
Usage:
  prepare-host.sh
  prepare-host.sh --skip-docker
  prepare-host.sh --skip-dxrt

Environment overrides:
  INSTALL_DOCKER=true|false
  INSTALL_DXRT=true|false
  DXRT_INSTALL_MODE=bundle|source
  DXRT_DIR
  DXRT_REPO
  DXRT_VENV_DIR
  DXRT_INSTALL_PREFIX
  ONNXLIB_DIRS
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --skip-docker)
      INSTALL_DOCKER=false
      shift
      ;;
    --skip-dxrt)
      INSTALL_DXRT=false
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

if [[ "$INSTALL_DOCKER" == "true" ]]; then
  "$SCRIPT_DIR/install-docker.sh"
fi

if [[ "$INSTALL_DXRT" == "true" ]]; then
  if [[ "$DXRT_INSTALL_MODE" == "source" ]]; then
    "$SCRIPT_DIR/install_dxrt_host.sh"
  else
    "$SCRIPT_DIR/install_dxrt_bundle.sh"
  fi
fi

if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 || true
fi

if [[ -x "$REPO_ROOT/scripts/linux/deploy-stack.sh" ]]; then
  sudo -u "${SUDO_USER:-root}" "$REPO_ROOT/scripts/linux/deploy-stack.sh" init || true
fi

cat <<EOF
Host preparation complete.

Next steps:
  1. Review $REPO_ROOT/deploy/.env
  2. Start the stack:
     cd $REPO_ROOT
     ./scripts/linux/deploy-stack.sh up
EOF

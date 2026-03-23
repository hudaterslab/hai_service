#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-root}"
SKIP_DOCKER=false
SKIP_DXRT=false
SKIP_UP=false
PREPARE_ONLY=false
USE_BUNDLED_IMAGES=true

usage() {
  cat <<'EOF'
Usage:
  sudo ./install.sh
  sudo ./install.sh --skip-docker
  sudo ./install.sh --skip-dxrt
  sudo ./install.sh --prepare-only
  sudo ./install.sh --skip-up
  sudo ./install.sh --no-bundled-images

This installer is intended for NAS-distributed edge packages.

What it does:
  1. creates deploy/.env for the current install path if missing
  2. prepares the host (Docker + DXRT)
  3. optionally loads bundled Docker images from docker-images/
  4. starts the VMS stack and shows a basic status check
EOF
}

ensure_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
  fi
}

ensure_env_file() {
  local env_file="$ROOT_DIR/deploy/.env"
  local env_example="$ROOT_DIR/deploy/.env.example"
  local escaped_root

  mkdir -p "$ROOT_DIR/runtime/media" "$ROOT_DIR/runtime/redis" "$ROOT_DIR/output/deploy-reports"

  if [[ ! -f "$env_file" ]]; then
    cp "$env_example" "$env_file"
    escaped_root="$(printf '%s\n' "$ROOT_DIR/runtime" | sed 's/[\/&]/\\&/g')"
    sed -i "s|^VMS_DATA_ROOT=.*$|VMS_DATA_ROOT=$escaped_root|" "$env_file"
  fi
}

load_bundled_images() {
  local image_dir="$ROOT_DIR/docker-images"
  local tar_file

  if [[ "$USE_BUNDLED_IMAGES" != "true" || ! -d "$image_dir" ]]; then
    return 0
  fi

  if compgen -G "$image_dir/*.tar" >/dev/null; then
    if [[ -f "$image_dir/PROJECT_NAME" ]]; then
      export VMS_COMPOSE_PROJECT_NAME
      VMS_COMPOSE_PROJECT_NAME="$(<"$image_dir/PROJECT_NAME")"
    fi
    export VMS_SKIP_BUILD=true
    for tar_file in "$image_dir"/*.tar; do
      docker load -i "$tar_file"
    done
  fi
}

install_launcher() {
  local target="/usr/local/bin/vms-edge"
  if [[ -e "$ROOT_DIR/vms-edge" ]]; then
    chmod +x "$ROOT_DIR/vms-edge"
    ln -sfn "$ROOT_DIR/vms-edge" "$target"
    echo "Installed launcher: $target -> $ROOT_DIR/vms-edge"
  fi
}

run_deploy_stack() {
  local -a env_args=()
  env_args+=("VMS_ALLOW_LOCAL_BUILD=true")
  [[ -n "${VMS_COMPOSE_PROJECT_NAME:-}" ]] && env_args+=("VMS_COMPOSE_PROJECT_NAME=$VMS_COMPOSE_PROJECT_NAME")
  [[ -n "${VMS_SKIP_BUILD:-}" ]] && env_args+=("VMS_SKIP_BUILD=$VMS_SKIP_BUILD")

  if [[ "$RUN_USER" == "root" ]]; then
    env "${env_args[@]}" "$ROOT_DIR/scripts/linux/deploy-stack.sh" "$@"
  else
    sudo -u "$RUN_USER" env "${env_args[@]}" "$ROOT_DIR/scripts/linux/deploy-stack.sh" "$@"
  fi
}

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
    --prepare-only)
      PREPARE_ONLY=true
      shift
      ;;
    --no-bundled-images)
      USE_BUNDLED_IMAGES=false
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

ensure_root
ensure_env_file
install_launcher

prepare_args=()
[[ "$SKIP_DOCKER" == "true" ]] && prepare_args+=(--skip-docker)
[[ "$SKIP_DXRT" == "true" ]] && prepare_args+=(--skip-dxrt)

"$ROOT_DIR/scripts/linux/prepare-host.sh" "${prepare_args[@]}"
load_bundled_images

if [[ "$PREPARE_ONLY" == "true" || "$SKIP_UP" == "true" ]]; then
  exit 0
fi

run_deploy_stack up
run_deploy_stack status
run_deploy_stack ctl monitor overview || true

echo ""
echo "Open the GUI at: http://127.0.0.1:8080/"
echo "Use the Docker CLI wrapper with:"
echo "  ./scripts/linux/deploy-stack.sh ctl --help"
echo "For patch updates later, use:"
echo "  ./update.sh --source <rsync-source>"

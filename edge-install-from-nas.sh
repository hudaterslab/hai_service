#!/usr/bin/env bash
set -euo pipefail

NAS_HOST="${NAS_HOST:-112.217.187.130}"
NAS_PORT="${NAS_PORT:-21423}"
NAS_USER="${NAS_USER:-dhkim}"
NAS_PASSWORD="${NAS_PASSWORD:-}"
NAS_REMOTE_DIR="${NAS_REMOTE_DIR:-/volume1/Hudaters/HanjinCCTV}"
LATEST_INSTALLER_FILE="${LATEST_INSTALLER_FILE:-LATEST_INSTALLER}"
RELEASE_DIR_NAME="${RELEASE_DIR_NAME:-vms-edge-current}"
PACKAGE_NAME="${PACKAGE_NAME:-}"
INSTALL_BASE_DIR="${INSTALL_BASE_DIR:-$HOME/vms-install}"
CURRENT_LINK_NAME="${CURRENT_LINK_NAME:-current}"
ACTION="${ACTION:-auto}"
SKIP_DOCKER=false
SKIP_DXRT=false
SKIP_UP=false
FORCE_DOWNLOAD=false
ASKPASS_SCRIPT=""

usage() {
  cat <<'EOF'
Usage:
  ./edge-install-from-nas.sh
  ./edge-install-from-nas.sh install
  ./edge-install-from-nas.sh update
  ./edge-install-from-nas.sh --host 112.217.187.130 --port 21423 --user dhkim
  ./edge-install-from-nas.sh --package vms-edge-installer-20260317.tar.gz
  ./edge-install-from-nas.sh --skip-docker
  ./edge-install-from-nas.sh --skip-dxrt

This helper:
  1. on a fresh edge node, downloads the latest installer tarball from NAS
  2. extracts it under ~/vms-install by default and runs sudo ./install.sh
  3. on an already installed node, updates from NAS vms-edge-current
  4. keeps ~/vms-install/current pointed at the active install tree
EOF
}

cleanup() {
  if [[ -n "$ASKPASS_SCRIPT" && -f "$ASKPASS_SCRIPT" ]]; then
    rm -f "$ASKPASS_SCRIPT"
  fi
}

trap cleanup EXIT

prepare_askpass() {
  if [[ -z "$NAS_PASSWORD" ]]; then
    return
  fi
  ASKPASS_SCRIPT="$(mktemp)"
  cat >"$ASKPASS_SCRIPT" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$SSH_PASSWORD_INPUT"
EOF
  chmod 700 "$ASKPASS_SCRIPT"
}

run_remote_cmd() {
  if [[ -n "$NAS_PASSWORD" ]]; then
    env SSH_PASSWORD_INPUT="$NAS_PASSWORD" DISPLAY=dummy SSH_ASKPASS="$ASKPASS_SCRIPT" SSH_ASKPASS_REQUIRE=force \
      setsid "$@" < /dev/null
  else
    "$@"
  fi
}

remote_release_dir() {
  printf '%s/%s' "${NAS_REMOTE_DIR%/}" "$RELEASE_DIR_NAME"
}

current_install_dir() {
  local link_path="$INSTALL_BASE_DIR/$CURRENT_LINK_NAME"
  if [[ -L "$link_path" ]]; then
    readlink -f "$link_path"
    return 0
  fi
  if [[ -d "$link_path" ]]; then
    printf '%s\n' "$link_path"
    return 0
  fi
  return 1
}

set_current_install_dir() {
  local install_dir="$1"
  ln -sfn "$install_dir" "$INSTALL_BASE_DIR/$CURRENT_LINK_NAME"
}

resolve_package_name() {
  if [[ -n "$PACKAGE_NAME" ]]; then
    printf '%s\n' "$PACKAGE_NAME"
    return 0
  fi

  local latest_path="${NAS_REMOTE_DIR%/}/$LATEST_INSTALLER_FILE"
  local latest_name=""
  latest_name="$(run_remote_cmd ssh -p "$NAS_PORT" -o StrictHostKeyChecking=accept-new "${NAS_USER}@${NAS_HOST}" "cat '$latest_path'" 2>/dev/null | tr -d '\r' | tail -n 1 | xargs || true)"
  if [[ -n "$latest_name" ]]; then
    printf '%s\n' "$latest_name"
    return 0
  fi

  echo "Could not resolve latest installer name from $latest_path" >&2
  echo "Pass --package <name.tar.gz> or upload $LATEST_INSTALLER_FILE to NAS." >&2
  exit 1
}

run_install() {
  local package_name="$1"
  local package_basename="${package_name%.tar.gz}"
  local install_args=()

  mkdir -p "$INSTALL_BASE_DIR"
  cd "$INSTALL_BASE_DIR"

  if [[ "$FORCE_DOWNLOAD" == "true" || ! -f "$package_name" ]]; then
    run_remote_cmd scp -O -P "$NAS_PORT" -o StrictHostKeyChecking=accept-new "${NAS_USER}@${NAS_HOST}:${NAS_REMOTE_DIR%/}/${package_name}" .
  fi

  rm -rf "$package_basename"
  tar -xzf "$package_name"
  cd "$package_basename"

  [[ "$SKIP_DOCKER" == "true" ]] && install_args+=(--skip-docker)
  [[ "$SKIP_DXRT" == "true" ]] && install_args+=(--skip-dxrt)
  [[ "$SKIP_UP" == "true" ]] && install_args+=(--skip-up)

  sudo ./install.sh "${install_args[@]}"
  set_current_install_dir "$INSTALL_BASE_DIR/$package_basename"
}

run_update() {
  local active_dir
  if ! active_dir="$(current_install_dir)"; then
    echo "No active install found under $INSTALL_BASE_DIR/$CURRENT_LINK_NAME" >&2
    return 1
  fi

  if [[ ! -x "$active_dir/edge-update-from-nas.sh" ]]; then
    echo "Active install is missing edge-update-from-nas.sh: $active_dir" >&2
    return 1
  fi

  local -a update_args=(
    --host "$NAS_HOST"
    --port "$NAS_PORT"
    --user "$NAS_USER"
    --remote-dir "$(remote_release_dir)"
  )
  [[ -n "$NAS_PASSWORD" ]] && update_args+=(--password "$NAS_PASSWORD")
  [[ "$SKIP_UP" == "true" ]] && update_args+=(--skip-up)

  "$active_dir/edge-update-from-nas.sh" "${update_args[@]}"
  set_current_install_dir "$active_dir"
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    install|update|auto)
      ACTION="$1"
      shift
      ;;
    --host)
      NAS_HOST="$2"
      shift 2
      ;;
    --port)
      NAS_PORT="$2"
      shift 2
      ;;
    --user)
      NAS_USER="$2"
      shift 2
      ;;
    --password)
      NAS_PASSWORD="$2"
      shift 2
      ;;
    --remote-dir)
      NAS_REMOTE_DIR="$2"
      shift 2
      ;;
    --package)
      PACKAGE_NAME="$2"
      PACKAGE_BASENAME="${PACKAGE_NAME%.tar.gz}"
      shift 2
      ;;
    --install-base-dir)
      INSTALL_BASE_DIR="$2"
      shift 2
      ;;
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
    --force-download)
      FORCE_DOWNLOAD=true
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

prepare_askpass
case "$ACTION" in
  auto)
    if current_install_dir >/dev/null 2>&1; then
      run_update
      echo "Edge update complete."
    else
      run_install "$(resolve_package_name)"
      echo "First-time install complete."
    fi
    ;;
  install)
    run_install "$(resolve_package_name)"
    echo "First-time install complete."
    ;;
  update)
    run_update
    echo "Edge update complete."
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage >&2
    exit 1
    ;;
esac

echo "Active install:"
echo "  $INSTALL_BASE_DIR/$CURRENT_LINK_NAME"
echo "Next time, re-run the same script to update in place."

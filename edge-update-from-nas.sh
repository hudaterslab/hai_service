#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAS_HOST="${NAS_HOST:-112.217.187.130}"
NAS_PORT="${NAS_PORT:-21423}"
NAS_USER="${NAS_USER:-dhkim}"
NAS_PASSWORD="${NAS_PASSWORD:-}"
NAS_REMOTE_DIR="${NAS_REMOTE_DIR:-/volume1/Hudaters/HanjinCCTV/vms-edge-current}"
STAGING_DIR="${STAGING_DIR:-$HOME/vms-install/vms-edge-current}"
SKIP_BUILD=false
SKIP_UP=false
KEEP_STAGING=false
PROMPT_PASSWORD=false
ASKPASS_SCRIPT=""

usage() {
  cat <<'EOF'
Usage:
  ./edge-update-from-nas.sh
  ./edge-update-from-nas.sh --host 112.217.187.130 --port 21423 --user dhkim
  ./edge-update-from-nas.sh --user dhkim --prompt-password
  ./edge-update-from-nas.sh --skip-build
  ./edge-update-from-nas.sh --skip-up

This helper:
  1. streams the latest vms-edge-current tree from NAS over SSH
  2. runs local update.sh against the downloaded tree without restarting
  3. optionally rebuilds local app images once
  4. starts the stack without an extra compose rebuild and checks health
EOF
}

cleanup() {
  if [[ -n "$ASKPASS_SCRIPT" && -f "$ASKPASS_SCRIPT" ]]; then
    rm -f "$ASKPASS_SCRIPT"
  fi
}

trap cleanup EXIT

ensure_password_prompt() {
  if [[ "$PROMPT_PASSWORD" == "true" && -z "$NAS_PASSWORD" ]]; then
    read -rsp "NAS 비밀번호: " NAS_PASSWORD
    echo
  fi
}

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

while [[ "$#" -gt 0 ]]; do
  case "$1" in
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
    --prompt-password)
      PROMPT_PASSWORD=true
      shift
      ;;
    --remote-dir)
      NAS_REMOTE_DIR="$2"
      shift 2
      ;;
    --staging-dir)
      STAGING_DIR="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    --skip-up)
      SKIP_UP=true
      shift
      ;;
    --keep-staging)
      KEEP_STAGING=true
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

if [[ "$SKIP_BUILD" == "true" && "$SKIP_UP" != "true" ]]; then
  echo "--skip-build requires --skip-up, otherwise the stack would restart with stale app images." >&2
  exit 1
fi

ensure_password_prompt
prepare_askpass

mkdir -p "$STAGING_DIR"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

if [[ -n "$NAS_PASSWORD" ]]; then
  env SSH_PASSWORD_INPUT="$NAS_PASSWORD" DISPLAY=dummy SSH_ASKPASS="$ASKPASS_SCRIPT" SSH_ASKPASS_REQUIRE=force \
    setsid ssh -p "$NAS_PORT" -o StrictHostKeyChecking=accept-new "${NAS_USER}@${NAS_HOST}" "cd '$NAS_REMOTE_DIR' && tar -cf - ." < /dev/null | tar -C "$STAGING_DIR" -xf -
else
  ssh -p "$NAS_PORT" -o StrictHostKeyChecking=accept-new "${NAS_USER}@${NAS_HOST}" "cd '$NAS_REMOTE_DIR' && tar -cf - ." | tar -C "$STAGING_DIR" -xf -
fi

"$ROOT_DIR/update.sh" --source "$STAGING_DIR/" --skip-restart

if [[ "$SKIP_BUILD" != "true" ]]; then
  sudo docker compose -f "$ROOT_DIR/deploy/docker-compose.yml" --env-file "$ROOT_DIR/deploy/.env" build \
    dxnn-host-infer vms-api vms-ops event-recorder delivery-worker
fi

if [[ "$SKIP_UP" != "true" ]]; then
  sudo env VMS_SKIP_BUILD=true "$ROOT_DIR/scripts/linux/deploy-stack.sh" up
  curl -fsS http://127.0.0.1:8080/healthz
fi

if [[ "$KEEP_STAGING" != "true" ]]; then
  rm -rf "$STAGING_DIR"
fi

echo "Update complete."

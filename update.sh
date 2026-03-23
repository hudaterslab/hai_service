#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${USER:-root}}"
SOURCE_PATH="${VMS_UPDATE_SOURCE:-}"
SSH_PORT="${VMS_UPDATE_SSH_PORT:-22}"
SSH_PASSWORD="${VMS_UPDATE_SSH_PASSWORD:-}"
ALLOW_BUILD=false
SKIP_RESTART=false
DRY_RUN=false
PROMPT_PASSWORD=false
ASKPASS_SCRIPT=""

usage() {
  cat <<'EOF'
Usage:
  ./update.sh --source <rsync-source>
  ./update.sh --source dhkim@nas:/volume1/Hudaters/HanjinCCTV/vms-edge-current/ --ssh-port 21423
  ./update.sh --source dhkim@nas:/volume1/Hudaters/HanjinCCTV/vms-edge-current/ --ssh-port 21423 --prompt-password
  sudo ./update.sh --source /mnt/nas/vms-edge-current/
  ./update.sh --dry-run --source <rsync-source>

This updater:
  1. rsyncs only changed app files into the current install path
  2. preserves deploy/.env, runtime/, output/, and local runtime state
  3. restarts the Docker stack unless --skip-restart is used

Options:
  --source <path>     rsync source path, local or remote
  --ssh-port <port>   SSH port for remote rsync sources, default: 22
  --password <pw>     remote SSH password for rsync source
  --prompt-password   prompt for remote SSH password
  --allow-build       explicitly allow docker compose rebuild during restart
  --dry-run           show planned rsync changes without applying them
  --skip-restart      sync files only, do not restart Docker services
EOF
}

cleanup() {
  if [[ -n "$ASKPASS_SCRIPT" && -f "$ASKPASS_SCRIPT" ]]; then
    rm -f "$ASKPASS_SCRIPT"
  fi
}

trap cleanup EXIT

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required tool: $1" >&2
    exit 1
  fi
}

run_deploy_stack() {
  local -a env_args=()
  if [[ "$ALLOW_BUILD" == "true" ]]; then
    env_args+=(VMS_ALLOW_LOCAL_BUILD=true)
  elif [[ "${VMS_SKIP_BUILD:-false}" == "true" ]]; then
    env_args+=(VMS_SKIP_BUILD=true)
  fi

  if [[ "${EUID:-$(id -u)}" -eq 0 && "$RUN_USER" != "root" ]]; then
    sudo -u "$RUN_USER" env "${env_args[@]}" "$ROOT_DIR/scripts/linux/deploy-stack.sh" "$@"
  else
    env "${env_args[@]}" "$ROOT_DIR/scripts/linux/deploy-stack.sh" "$@"
  fi
}

ensure_password_prompt() {
  if [[ "$PROMPT_PASSWORD" == "true" && -z "$SSH_PASSWORD" ]]; then
    read -rsp "원격 SSH 비밀번호: " SSH_PASSWORD
    echo
  fi
}

prepare_askpass() {
  if [[ -z "$SSH_PASSWORD" ]]; then
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
    --source)
      SOURCE_PATH="$2"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="$2"
      shift 2
      ;;
    --password)
      SSH_PASSWORD="$2"
      shift 2
      ;;
    --prompt-password)
      PROMPT_PASSWORD=true
      shift
      ;;
    --allow-build)
      ALLOW_BUILD=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --skip-restart)
      SKIP_RESTART=true
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

if [[ -z "$SOURCE_PATH" ]]; then
  echo "--source is required" >&2
  usage >&2
  exit 1
fi

if [[ "$DRY_RUN" != "true" && "$SKIP_RESTART" != "true" && "$ALLOW_BUILD" != "true" && "${VMS_SKIP_BUILD:-false}" != "true" ]]; then
  echo "Refusing automatic restart after update without an explicit image strategy." >&2
  echo "Use one of:" >&2
  echo "  ./update.sh --allow-build --source <rsync-source>" >&2
  echo "  VMS_SKIP_BUILD=true ./update.sh --source <rsync-source>" >&2
  echo "  ./update.sh --skip-restart --source <rsync-source>" >&2
  exit 1
fi

require_tool rsync

ensure_password_prompt
prepare_askpass

RSYNC_ARGS=(
  -a
  --delete
  --itemize-changes
  --exclude-from="$ROOT_DIR/scripts/linux/rsync-update.exclude"
)

if [[ "$DRY_RUN" == "true" ]]; then
  RSYNC_ARGS+=(--dry-run)
fi

if [[ "$SOURCE_PATH" == *:* ]]; then
  RSYNC_ARGS+=(-e "ssh -p $SSH_PORT -o StrictHostKeyChecking=accept-new")
fi

if [[ -n "$SSH_PASSWORD" && "$SOURCE_PATH" == *:* ]]; then
  env SSH_PASSWORD_INPUT="$SSH_PASSWORD" DISPLAY=dummy SSH_ASKPASS="$ASKPASS_SCRIPT" SSH_ASKPASS_REQUIRE=force \
    rsync "${RSYNC_ARGS[@]}" "$SOURCE_PATH" "$ROOT_DIR/"
else
  rsync "${RSYNC_ARGS[@]}" "$SOURCE_PATH" "$ROOT_DIR/"
fi

if [[ "$DRY_RUN" == "true" || "$SKIP_RESTART" == "true" ]]; then
  exit 0
fi

run_deploy_stack up
run_deploy_stack status

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RELEASE_NAME="${RELEASE_NAME:-vms-edge-current}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist}"
SOURCE_DIR="$OUTPUT_DIR/$RELEASE_NAME/"
TARGET_PATH="${TARGET_PATH:-}"
SSH_PORT="${SSH_PORT:-22}"
SSH_PASSWORD="${SSH_PASSWORD:-}"
PROMPT_PASSWORD=false
ASKPASS_SCRIPT=""

usage() {
  cat <<'EOF'
Usage:
  publish-rsync-release.sh --target dhkim@nas:/volume1/Hudaters/HanjinCCTV/vms-edge-current/
  publish-rsync-release.sh --target /mnt/nas/vms-edge-current/
  publish-rsync-release.sh --target <path> --ssh-port 21423
  publish-rsync-release.sh --target dhkim@nas:/volume1/Hudaters/HanjinCCTV/vms-edge-current/ --ssh-port 21423 --prompt-password

This script builds the rsync release tree and publishes it to the target path.
EOF
}

cleanup() {
  if [[ -n "$ASKPASS_SCRIPT" && -f "$ASKPASS_SCRIPT" ]]; then
    rm -f "$ASKPASS_SCRIPT"
  fi
}

trap cleanup EXIT

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
    --target)
      TARGET_PATH="$2"
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

if [[ -z "$TARGET_PATH" ]]; then
  echo "--target is required" >&2
  usage >&2
  exit 1
fi

ensure_password_prompt
prepare_askpass

"$SCRIPT_DIR/build-rsync-release.sh" --output-dir "$OUTPUT_DIR" --release-name "$RELEASE_NAME"

RSYNC_ARGS=(-a --delete --itemize-changes)
if [[ "$TARGET_PATH" == *:* ]]; then
  RSYNC_ARGS+=(-e "ssh -p $SSH_PORT -o StrictHostKeyChecking=accept-new")
fi

if [[ -n "$SSH_PASSWORD" && "$TARGET_PATH" == *:* ]]; then
  env SSH_PASSWORD_INPUT="$SSH_PASSWORD" DISPLAY=dummy SSH_ASKPASS="$ASKPASS_SCRIPT" SSH_ASKPASS_REQUIRE=force \
    rsync "${RSYNC_ARGS[@]}" "$SOURCE_DIR" "$TARGET_PATH"
else
  rsync "${RSYNC_ARGS[@]}" "$SOURCE_DIR" "$TARGET_PATH"
fi

echo "Published rsync release to:"
echo "  $TARGET_PATH"

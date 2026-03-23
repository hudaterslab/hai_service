#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8080}"
MODEL_PATH_DEFAULT="/opt/vms/models/hf/HudatersU_Safety_helmet/safety_helmet_20260320.dxnn"
MODEL_PATH="${MODEL_PATH:-$MODEL_PATH_DEFAULT}"
ENABLED="${ENABLED:-true}"
TIMEOUT_SEC="${TIMEOUT_SEC:-5}"
POLL_SEC="${POLL_SEC:-2}"
COOLDOWN_SEC="${COOLDOWN_SEC:-10}"

usage() {
  cat <<'EOF'
Usage:
  ./set-edge-ai-model.sh show
  ./set-edge-ai-model.sh list
  ./set-edge-ai-model.sh enable
  ./set-edge-ai-model.sh disable
  ./set-edge-ai-model.sh set --model-path /opt/vms/models/your_model.dxnn

Environment:
  API_BASE_URL   Default: http://127.0.0.1:8080
  MODEL_PATH     Default: /opt/vms/models/hf/HudatersU_Safety_helmet/safety_helmet_20260320.dxnn
  TIMEOUT_SEC    Default: 5
  POLL_SEC       Default: 2
  COOLDOWN_SEC   Default: 10
EOF
}

require_curl() {
  command -v curl >/dev/null 2>&1 || {
    echo "curl is required" >&2
    exit 1
  }
}

show_settings() {
  curl -fsS "$API_BASE_URL/settings/ai-model"
  echo
}

list_models() {
  curl -fsS "$API_BASE_URL/models/list"
  echo
}

put_settings() {
  local enabled="$1"
  local model_path="$2"
  curl -fsS -X PUT "$API_BASE_URL/settings/ai-model" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{"enabled":$enabled,"modelPath":"$model_path","timeoutSec":$TIMEOUT_SEC,"pollSec":$POLL_SEC,"cooldownSec":$COOLDOWN_SEC}
EOF
  echo
}

cmd="${1:-show}"
shift || true

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --model-path)
      MODEL_PATH="$2"
      shift 2
      ;;
    --api-base-url)
      API_BASE_URL="$2"
      shift 2
      ;;
    --timeout-sec)
      TIMEOUT_SEC="$2"
      shift 2
      ;;
    --poll-sec)
      POLL_SEC="$2"
      shift 2
      ;;
    --cooldown-sec)
      COOLDOWN_SEC="$2"
      shift 2
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

require_curl

case "$cmd" in
  show)
    show_settings
    ;;
  list)
    list_models
    ;;
  enable)
    put_settings true "$MODEL_PATH"
    ;;
  disable)
    put_settings false ""
    ;;
  set)
    put_settings "$ENABLED" "$MODEL_PATH"
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac

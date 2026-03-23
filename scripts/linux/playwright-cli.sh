#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$REPO_ROOT/playwright-cli.json"

# Load the repo-local Node.js and Playwright browser paths.
source "$REPO_ROOT/runtime/tooling/env.playwright.sh"

cmd_args=()
has_config_flag="false"
for arg in "$@"; do
  case "$arg" in
    --config|--config=*)
      has_config_flag="true"
      ;;
  esac
done

if [[ "$has_config_flag" != "true" && -f "$CONFIG_FILE" ]]; then
  cmd_args+=(--config "$CONFIG_FILE")
fi

cmd_args+=("$@")

if command -v playwright-cli >/dev/null 2>&1; then
  exec playwright-cli "${cmd_args[@]}"
fi

exec npx --yes --package @playwright/cli playwright-cli "${cmd_args[@]}"

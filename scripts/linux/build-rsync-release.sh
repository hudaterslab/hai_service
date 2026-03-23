#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist}"
RELEASE_NAME="${RELEASE_NAME:-vms-edge-current}"
STAGE_DIR="$OUTPUT_DIR/$RELEASE_NAME"

usage() {
  cat <<'EOF'
Usage:
  build-rsync-release.sh
  build-rsync-release.sh --output-dir /path/to/dist
  build-rsync-release.sh --release-name vms-edge-current

Build an rsync-friendly release tree for patch updates. The resulting directory
can be placed on NAS and consumed by update.sh from an installed edge node.
EOF
}

copy_path() {
  local rel="$1"
  if [[ ! -e "$REPO_ROOT/$rel" ]]; then
    return 0
  fi
  mkdir -p "$STAGE_DIR"
  tar -C "$REPO_ROOT" -cf - "$rel" | tar -C "$STAGE_DIR" -xf -
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --release-name)
      RELEASE_NAME="$2"
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

STAGE_DIR="$OUTPUT_DIR/$RELEASE_NAME"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

copy_path README.md
copy_path edge-install-from-nas.sh
copy_path edge-update-from-nas.sh
copy_path install.sh
copy_path update.sh
copy_path vms-edge
copy_path config
copy_path db
copy_path deploy
copy_path models
copy_path openapi
copy_path runtime/dxrt-install
copy_path scripts
copy_path services
copy_path yolov8n.pt

rm -f "$STAGE_DIR/deploy/.env"
rm -rf "$STAGE_DIR/runtime/media" "$STAGE_DIR/runtime/redis" "$STAGE_DIR/runtime/postgres"
rm -rf "$STAGE_DIR/models/.runtime" "$STAGE_DIR/output" "$STAGE_DIR/dist" "$STAGE_DIR/tmp"
find "$STAGE_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE_DIR" -type f -name '*.pyc' -delete

echo "Rsync release created:"
echo "  $STAGE_DIR"

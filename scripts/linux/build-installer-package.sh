#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist}"
PACKAGE_NAME="${PACKAGE_NAME:-vms-edge-installer-$(date +%Y%m%d)}"
INCLUDE_IMAGES=false
IMAGE_PROJECT_NAME="${VMS_COMPOSE_PROJECT_NAME:-vmsedge}"
ENV_FILE="$REPO_ROOT/deploy/.env"
ENV_EXAMPLE="$REPO_ROOT/deploy/.env.example"

usage() {
  cat <<'EOF'
Usage:
  build-installer-package.sh
  build-installer-package.sh --include-images
  build-installer-package.sh --output-dir /path/to/dist
  build-installer-package.sh --package-name custom-name
  build-installer-package.sh --image-project-name vmsedge

This script creates a NAS-uploadable installer tarball that can be unpacked on
another edge host and started with:

  sudo ./install.sh

Options:
  --include-images           Build Compose images and bundle them as docker save tar files.
  --output-dir <dir>         Dist directory to place the staged package and tarball.
  --package-name <name>      Package directory and tarball base name.
  --image-project-name <n>   Compose project name to use when exporting images.
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

write_manifest() {
  cat > "$STAGE_DIR/INSTALLER_MANIFEST.txt" <<EOF
Package: $PACKAGE_NAME
Built at: $(date '+%Y-%m-%d %H:%M:%S %z')
Source repo: $REPO_ROOT

Included top-level items:
- config
- db
- deploy
- models
- openapi
- runtime/dxrt-install
- scripts
- services
- edge-install-from-nas.sh
- edge-update-from-nas.sh
- install.sh
- update.sh
- vms-edge
- README.md

Runtime notes:
- deploy/.env is not bundled from the source machine.
- install.sh creates deploy/.env with VMS_DATA_ROOT pointed at the unpacked path.
- If docker-images/ exists, install.sh loads those images and starts Compose with VMS_SKIP_BUILD=true.
EOF
}

bundle_images() {
  local image_dir="$STAGE_DIR/docker-images"
  local service
  mkdir -p "$image_dir"

  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not installed. Cannot use --include-images." >&2
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    echo "docker compose plugin is not available. Cannot use --include-images." >&2
    exit 1
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
  fi

  VMS_COMPOSE_PROJECT_NAME="$IMAGE_PROJECT_NAME" "$REPO_ROOT/scripts/linux/deploy-stack.sh" init
  docker compose --project-name "$IMAGE_PROJECT_NAME" \
    -f "$REPO_ROOT/deploy/docker-compose.yml" \
    --env-file "$ENV_FILE" \
    build

  for service in dxnn-host-infer vms-api vms-ops event-recorder delivery-worker; do
    docker save -o "$image_dir/${service}.tar" "${IMAGE_PROJECT_NAME}-${service}"
  done

  printf '%s\n' "$IMAGE_PROJECT_NAME" > "$image_dir/PROJECT_NAME"
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --include-images)
      INCLUDE_IMAGES=true
      shift
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --package-name)
      PACKAGE_NAME="$2"
      shift 2
      ;;
    --image-project-name)
      IMAGE_PROJECT_NAME="$2"
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

STAGE_DIR="$OUTPUT_DIR/$PACKAGE_NAME"
ARCHIVE_PATH="$OUTPUT_DIR/$PACKAGE_NAME.tar.gz"
TMP_ARCHIVE_PATH=""

mkdir -p "$OUTPUT_DIR"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/runtime" "$STAGE_DIR/output/deploy-reports"

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
copy_path scripts
copy_path services
copy_path runtime/dxrt-install
copy_path yolov8n.pt

rm -f "$STAGE_DIR/deploy/.env"
rm -rf "$STAGE_DIR/models/.runtime"
find "$STAGE_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE_DIR" -type f -name '*.pyc' -delete

write_manifest

if [[ "$INCLUDE_IMAGES" == "true" ]]; then
  bundle_images
fi

TMP_ARCHIVE_PATH="$(mktemp "/tmp/${PACKAGE_NAME}.XXXXXX.tar.gz")"
tar -C "$OUTPUT_DIR" -czf "$TMP_ARCHIVE_PATH" "$PACKAGE_NAME"
mv -f "$TMP_ARCHIVE_PATH" "$ARCHIVE_PATH"
printf '%s\n' "$PACKAGE_NAME.tar.gz" > "$OUTPUT_DIR/LATEST_INSTALLER"
cp "$REPO_ROOT/edge-install-from-nas.sh" "$OUTPUT_DIR/edge-install-from-nas.sh"

echo "Installer package created:"
echo "  $ARCHIVE_PATH"
echo "Bootstrap metadata updated:"
echo "  $OUTPUT_DIR/LATEST_INSTALLER"

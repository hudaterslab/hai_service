#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="${1:-/opt/deepx/dx_rt/python_package}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DST_DIR="$REPO_ROOT/runtime/dxrt-install/python_package"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "DXRT python_package not found: $SRC_DIR" >&2
  exit 1
fi

rm -rf "$DST_DIR"
mkdir -p "$DST_DIR"
cp -a "$SRC_DIR/." "$DST_DIR/"
echo "Vendored DXRT python_package into $DST_DIR"

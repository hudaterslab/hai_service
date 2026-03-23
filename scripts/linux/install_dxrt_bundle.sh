#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/dxrt_common.sh"
BUNDLE_DIR="${DXRT_BUNDLE_DIR:-$REPO_ROOT/runtime/dxrt-install}"
DXRT_DIR="${DXRT_DIR:-/opt/deepx/dx_rt}"
DXRT_HOST_LIB_DIR="${DXRT_HOST_LIB_DIR:-/usr/local/lib}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FALLBACK_TO_SOURCE="${FALLBACK_TO_SOURCE:-true}"

ensure_compat_layout() {
  mkdir -p "$DXRT_DIR/lib" "$DXRT_DIR/extern"

  # Some dx_engine builds expect headers under lib/include and extern/include.
  if [[ -d "$DXRT_DIR/include" && ! -e "$DXRT_DIR/lib/include" ]]; then
    ln -sfn ../include "$DXRT_DIR/lib/include"
  fi
  if [[ -d "$DXRT_DIR/include/dxrt/extern" && ! -e "$DXRT_DIR/extern/include" ]]; then
    ln -sfn ../include/dxrt/extern "$DXRT_DIR/extern/include"
  fi
}

host_has_dxrt_driver() {
  if [[ -e /dev/dxrt0 ]]; then
    return 0
  fi

  if command -v dpkg-query >/dev/null 2>&1; then
    if dpkg-query -W -f='${Status}' dxrt-driver-dkms 2>/dev/null | grep -q "install ok installed"; then
      return 0
    fi
  fi

  if command -v lsmod >/dev/null 2>&1; then
    if lsmod | grep -Eq '^dx(rt_driver|_dma)\\b'; then
      return 0
    fi
  fi

  return 1
}

if [[ ! -d "$BUNDLE_DIR" ]]; then
  echo "[dxrt-bundle] bundle not found: $BUNDLE_DIR" >&2
  if [[ "$FALLBACK_TO_SOURCE" == "true" ]]; then
    exec "$SCRIPT_DIR/install_dxrt_host.sh"
  fi
  exit 1
fi

echo "[dxrt-bundle] installing DXRT bundle from $BUNDLE_DIR"
if ! host_has_dxrt_driver; then
  echo "[dxrt-bundle] host DXRT driver not detected"
  if [[ "$FALLBACK_TO_SOURCE" == "true" ]]; then
    echo "[dxrt-bundle] falling back to source installer for driver/firmware setup"
    exec "$SCRIPT_DIR/install_dxrt_host.sh"
  fi
  echo "[dxrt-bundle] fallback disabled; cannot continue on a clean host" >&2
  exit 1
fi

mkdir -p "$(dirname "$DXRT_DIR")" "$DXRT_HOST_LIB_DIR"
rm -rf "$DXRT_DIR"
cp -a "$BUNDLE_DIR" "$DXRT_DIR"
ensure_compat_layout

if [[ -f "$DXRT_DIR/lib/libdxrt.so" ]]; then
  dxrt_sync_host_libs "$DXRT_DIR" "$DXRT_HOST_LIB_DIR"
fi

if [[ -d "$DXRT_DIR/python_package" ]]; then
  echo "[dxrt-bundle] python_package found, validating dx_engine install path"
  if ! "$PYTHON_BIN" -m pip install --break-system-packages "$DXRT_DIR/python_package"; then
    echo "[dxrt-bundle] python_package install failed" >&2
    echo "[dxrt-bundle] expected headers:" >&2
    echo "  $DXRT_DIR/include/dxrt/dxrt_api.h" >&2
    echo "  $DXRT_DIR/lib/include/dxrt/dxrt_api.h" >&2
    echo "  $DXRT_DIR/extern/include" >&2
    ls -ld "$DXRT_DIR/include" "$DXRT_DIR/lib" "$DXRT_DIR/lib/include" "$DXRT_DIR/extern" "$DXRT_DIR/extern/include" 2>/dev/null || true
    ls -l "$DXRT_DIR/include/dxrt/dxrt_api.h" "$DXRT_DIR/lib/include/dxrt/dxrt_api.h" 2>/dev/null || true
  fi
fi

if "$PYTHON_BIN" -c "import dx_engine" >/dev/null 2>&1; then
  echo "[dxrt-bundle] dx_engine import OK"
  dxrt_validate_install "$PYTHON_BIN" "$DXRT_DIR" "$DXRT_HOST_LIB_DIR"
  exit 0
fi

echo "[dxrt-bundle] dx_engine is still unavailable after bundle copy"
if [[ "$FALLBACK_TO_SOURCE" == "true" ]]; then
  echo "[dxrt-bundle] falling back to source installer"
  exec "$SCRIPT_DIR/install_dxrt_host.sh"
fi

echo "[dxrt-bundle] fallback disabled; install incomplete" >&2
exit 1

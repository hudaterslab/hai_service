#!/bin/sh
set -eu

DXRT_AUTO_PIP_INSTALL="${DXRT_AUTO_PIP_INSTALL:-true}"
DXRT_PY_PACKAGE_PATH="${DXRT_PY_PACKAGE_PATH:-/opt/dxrt/python_package}"
DXRT_HOST_LIB_PATH="${DXRT_HOST_LIB_PATH:-/opt/dxrt_host_lib}"
DXRT_FORCE_PIP_REINSTALL="${DXRT_FORCE_PIP_REINSTALL:-false}"

if [ -d "$DXRT_HOST_LIB_PATH" ]; then
  export LD_LIBRARY_PATH="${DXRT_HOST_LIB_PATH}:${LD_LIBRARY_PATH:-}"
  export LIBRARY_PATH="${DXRT_HOST_LIB_PATH}:${LIBRARY_PATH:-}"
  export LDFLAGS="-L${DXRT_HOST_LIB_PATH} ${LDFLAGS:-}"
  export CMAKE_LIBRARY_PATH="${DXRT_HOST_LIB_PATH}:${CMAKE_LIBRARY_PATH:-}"
fi

if [ "$DXRT_AUTO_PIP_INSTALL" = "true" ]; then
  if [ "$DXRT_FORCE_PIP_REINSTALL" != "true" ] && python -c "import dx_engine" >/dev/null 2>&1; then
    echo "[dxnn-host-entrypoint] dx_engine already available"
  elif [ -d "$DXRT_PY_PACKAGE_PATH" ]; then
    DXRT_ROOT_DIR="$(dirname "$DXRT_PY_PACKAGE_PATH")"
    BUILD_ROOT_DIR="/tmp/dxrt_build"
    BUILD_PKG_DIR="${BUILD_ROOT_DIR}/python_package"
    rm -rf "$BUILD_ROOT_DIR"
    cp -a "$DXRT_ROOT_DIR" "$BUILD_ROOT_DIR"
    echo "[dxnn-host-entrypoint] installing dx_engine from $BUILD_PKG_DIR"
    python -m pip uninstall -y dx_engine dx-engine >/dev/null 2>&1 || true
    python -m pip install --no-cache-dir --break-system-packages "$BUILD_PKG_DIR" \
      || python -m pip install --no-cache-dir "$BUILD_PKG_DIR" \
      || echo "[dxnn-host-entrypoint] warning: dx_engine install failed"
  else
    echo "[dxnn-host-entrypoint] warning: $DXRT_PY_PACKAGE_PATH not found, skip dx_engine install"
  fi
fi

exec python /app/dxnn_host_infer_service.py

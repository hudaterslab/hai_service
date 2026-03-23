#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/dxrt_common.sh"

DXRT_DIR="${DXRT_DIR:-/opt/deepx/dx_rt}"
DXRT_REPO="${DXRT_REPO:-https://github.com/DEEPX-AI/dx_rt.git}"
DX_RUNTIME_REPO="${DX_RUNTIME_REPO:-https://github.com/DEEPX-AI/dx-runtime.git}"
DX_RUNTIME_COMMIT="${DX_RUNTIME_COMMIT:-28aa8d1cc0ae23587493e83ff6586d6d519a951a}"
DX_RUNTIME_DIR="${DX_RUNTIME_DIR:-/opt/deepx/dx-runtime}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DXRT_VENV_DIR="${DXRT_VENV_DIR:-/opt/vms/venv-dx-runtime}"
DXRT_INSTALL_PREFIX="${DXRT_INSTALL_PREFIX:-/usr/local}"
DXRT_HOST_LIB_DIR="${DXRT_HOST_LIB_DIR:-${DXRT_INSTALL_PREFIX}/lib}"
ONNXLIB_DIRS="${ONNXLIB_DIRS:-}"
DXRT_DRIVER_SOURCE_BUILD="${DXRT_DRIVER_SOURCE_BUILD:-false}"

sanitize_build_env() {
  local sanitized=()
  local entry

  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo "[dxrt-host] deactivating active virtualenv: ${VIRTUAL_ENV}"
    for entry in ${PATH//:/ }; do
      if [[ "$entry" == "$VIRTUAL_ENV/bin" ]]; then
        continue
      fi
      sanitized+=("$entry")
    done
    PATH="$(IFS=:; echo "${sanitized[*]}")"
  fi

  unset VIRTUAL_ENV
  unset PYTHONHOME
  unset PYTHONPATH
  hash -r
}

refresh_dxrt_checkout() {
  local repo_url="$1"
  local target_dir="$2"
  local tmp_dir

  mkdir -p "$target_dir"
  tmp_dir="$(mktemp -d "$(dirname "$target_dir")/.dxrt-clone.XXXXXX")"
  git clone --depth 1 "$repo_url" "$tmp_dir"

  find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a "$tmp_dir"/. "$target_dir"/
  rm -rf "$tmp_dir"
}

refresh_dx_runtime_checkout() {
  local repo_url="$1"
  local target_dir="$2"
  local commit="$3"

  mkdir -p "$(dirname "$target_dir")"
  rm -rf "$target_dir"
  git clone --recurse-submodules "$repo_url" "$target_dir"
  git -C "$target_dir" checkout "$commit"
  git -C "$target_dir" submodule update --init --recursive
}

host_has_dxrt_device() {
  if [[ -e /dev/dxrt0 ]]; then
    return 0
  fi

  if command -v lsmod >/dev/null 2>&1 && lsmod | grep -Eq '^dx(rt_driver|_dma)\b'; then
    return 0
  fi

  return 1
}

install_dxrt_driver_stack() {
  local -a install_args=(
    --target=dx_rt_npu_linux_driver
    --skip-uninstall
    --sanity-check=n
  )

  if [[ "$DXRT_DRIVER_SOURCE_BUILD" == "true" ]]; then
    install_args+=(--driver-source-build)
  fi

  echo "[dxrt-host] preparing DXRT driver stack from ${DX_RUNTIME_REPO}@${DX_RUNTIME_COMMIT}"
  refresh_dx_runtime_checkout "$DX_RUNTIME_REPO" "$DX_RUNTIME_DIR" "$DX_RUNTIME_COMMIT"
  (
    cd "$DX_RUNTIME_DIR"
    bash ./install.sh "${install_args[@]}"
  )
}

ensure_onnxruntime_layout() {
  local target_arch="$1"
  local onnx_dir_name="onnxruntime_${target_arch}"
  local onnx_dir="${DXRT_DIR}/util/${onnx_dir_name}"
  local onnx_lib="${onnx_dir}/lib/libonnxruntime.so"

  if [[ -f "$onnx_lib" ]]; then
    echo "[dxrt-host] upstream ONNXRuntime bundle already present: $onnx_lib"
    return 0
  fi

  echo "[dxrt-host] preparing upstream ONNXRuntime bundle for ${target_arch}"
  if ! bash ./install.sh --onnxruntime --arch "${target_arch}"; then
    dxrt_warn "install.sh --onnxruntime failed; falling back to externally installed onnxruntime paths"
  fi

  if [[ -f "$onnx_lib" ]]; then
    echo "[dxrt-host] upstream ONNXRuntime bundle ready: $onnx_lib"
    return 0
  fi

  if [[ -z "${ONNXLIB_DIRS}" ]]; then
    ONNXLIB_DIRS="$(dxrt_find_onnxlib_dirs "${PYTHON_BIN}" || true)"
  fi
  if [[ -z "${ONNXLIB_DIRS}" ]]; then
    dxrt_fail "onnxruntime library path not detected after bundle install; export ONNXLIB_DIRS manually"
  fi

  local lib_dir=""
  local include_dir=""
  local candidate
  IFS=':' read -r -a onnx_candidates <<< "${ONNXLIB_DIRS}"
  for candidate in "${onnx_candidates[@]}"; do
    [[ -d "$candidate" ]] || continue
    if [[ -f "$candidate/libonnxruntime.so" || -f "$candidate/libonnxruntime.so.1" ]]; then
      lib_dir="$candidate"
      break
    fi
  done

  for candidate in \
    "${lib_dir%/lib}/include" \
    "${lib_dir%/capi}/include" \
    "/usr/local/include" \
    "/usr/include" \
    "/usr/include/onnxruntime" \
    "/usr/local/include/onnxruntime"
  do
    if [[ -f "${candidate}/onnxruntime_c_api.h" || -f "${candidate}/onnxruntime/core/session/onnxruntime_c_api.h" ]]; then
      include_dir="$candidate"
      break
    fi
  done

  if [[ -z "$lib_dir" || -z "$include_dir" ]]; then
    dxrt_fail "unable to synthesize ONNXRuntime layout for DXRT build (lib_dir='${lib_dir}', include_dir='${include_dir}')"
  fi

  mkdir -p "${onnx_dir}/lib" "${onnx_dir}/include"
  cp -a "${lib_dir}/." "${onnx_dir}/lib/"
  cp -a "${include_dir}/." "${onnx_dir}/include/"
  echo "[dxrt-host] synthesized ONNXRuntime bundle from lib=${lib_dir} include=${include_dir}"
}

echo "[dxrt-host] start install/check"
echo "[dxrt-host] DXRT_DIR=${DXRT_DIR}"
echo "[dxrt-host] PYTHON_BIN=${PYTHON_BIN}"
sanitize_build_env
echo "[dxrt-host] cmake=$(command -v cmake || echo missing)"
echo "[dxrt-host] ninja=$(command -v ninja || echo missing)"

if ! host_has_dxrt_device; then
  echo "[dxrt-host] DXRT device not detected; installing driver stack first"
  install_dxrt_driver_stack
  if ! host_has_dxrt_device; then
    dxrt_fail "DXRT driver installed but device is still not visible. Reboot or cold boot the host, confirm /dev/dxrt0 exists, then rerun the installer."
  fi
fi

if ${PYTHON_BIN} -c "import dx_engine" >/dev/null 2>&1; then
  echo "[dxrt-host] dx_engine already installed"
  exit 0
fi

if [ -x "${DXRT_VENV_DIR}/bin/python" ] && "${DXRT_VENV_DIR}/bin/python" -c "import dx_engine" >/dev/null 2>&1; then
  echo "[dxrt-host] dx_engine already available in ${DXRT_VENV_DIR}"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  git \
  build-essential \
  cmake \
  ninja-build \
  pkg-config \
  python3 \
  python3-dev \
  python3-pip \
  python3-setuptools \
  python3-wheel \
  libncurses5-dev \
  libncursesw5-dev \
  libcurl4-openssl-dev \
  zlib1g-dev \
  python3-venv

if ! ${PYTHON_BIN} -c "import onnxruntime" >/dev/null 2>&1; then
  ${PYTHON_BIN} -m pip install --break-system-packages onnxruntime \
    || ${PYTHON_BIN} -m pip install onnxruntime
fi
if ! ${PYTHON_BIN} -c "import onnxruntime" >/dev/null 2>&1; then
  dxrt_fail "onnxruntime import failed; set ONNXLIB_DIRS manually if the package is installed in a non-standard location"
fi

mkdir -p "$(dirname "${DXRT_DIR}")"
if [ ! -d "${DXRT_DIR}/.git" ]; then
  refresh_dxrt_checkout "${DXRT_REPO}" "${DXRT_DIR}"
else
  git -C "${DXRT_DIR}" pull --ff-only || dxrt_warn "git pull failed; using existing ${DXRT_DIR} checkout"
fi

cd "${DXRT_DIR}"

echo "[dxrt-host] running dependency installer"
if ! bash ./install.sh --dep; then
  dxrt_warn "install.sh --dep failed; continuing with already installed host packages"
fi

TARGET_ARCH="$(dxrt_map_arch "$(uname -m)")"
ensure_onnxruntime_layout "${TARGET_ARCH}"

if [ -z "${ONNXLIB_DIRS}" ]; then
  ONNXLIB_DIRS="$(dxrt_find_onnxlib_dirs "${PYTHON_BIN}" || true)"
fi
if [ -n "${ONNXLIB_DIRS}" ]; then
  export ONNXLIB_DIRS
  echo "[dxrt-host] ONNXLIB_DIRS=${ONNXLIB_DIRS}"
else
  dxrt_fail "ONNXLIB_DIRS not detected automatically; export ONNXLIB_DIRS before running this installer"
fi

echo "[dxrt-host] building/installing dxrt runtime"
env -u VIRTUAL_ENV -u PYTHONHOME -u PYTHONPATH PATH="$PATH" bash ./build.sh \
  --type Release \
  --arch "${TARGET_ARCH}" \
  --install "${DXRT_INSTALL_PREFIX}" \
  --python-exec "${PYTHON_BIN}" \
  --python-break-system-packages

mkdir -p "$(dirname "${DXRT_VENV_DIR}")"
if [ ! -x "${DXRT_VENV_DIR}/bin/python" ]; then
  ${PYTHON_BIN} -m venv "${DXRT_VENV_DIR}"
fi
"${DXRT_VENV_DIR}/bin/pip" install --upgrade pip
"${DXRT_VENV_DIR}/bin/pip" install --break-system-packages opencv-python-headless numpy

if "${PYTHON_BIN}" -c "import dx_engine, pathlib; print(pathlib.Path(dx_engine.__file__).resolve())" >/dev/null 2>&1; then
  DX_ENGINE_PATH="$("${PYTHON_BIN}" -c "import dx_engine, pathlib; print(pathlib.Path(dx_engine.__file__).resolve().parent.parent)")"
  if [ -d "${DX_ENGINE_PATH}" ]; then
    SITE_PACKAGES_REL="$("${DXRT_VENV_DIR}/bin/python" - <<'PY'
import sys
print(f"python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
PY
)"
    SITE_PACKAGES_DIR="${DXRT_VENV_DIR}/lib/${SITE_PACKAGES_REL}"
    mkdir -p "${SITE_PACKAGES_DIR}"
    echo "[dxrt-host] mirroring dx_engine package from ${DX_ENGINE_PATH} into venv"
    cp -a "${DX_ENGINE_PATH}/." "${SITE_PACKAGES_DIR}/"
  fi
fi

echo "[dxrt-host] validating dxrt runtime"
dxrt_validate_install "${PYTHON_BIN}" "${DXRT_DIR}" "${DXRT_HOST_LIB_DIR}" "${DXRT_VENV_DIR}"
dxrt_restart_runtime_containers
echo "[dxrt-host] done"

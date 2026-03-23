#!/usr/bin/env bash

dxrt_log() {
  echo "[dxrt] $*"
}

dxrt_warn() {
  echo "[dxrt] warning: $*" >&2
}

dxrt_fail() {
  echo "[dxrt] error: $*" >&2
  exit 1
}

dxrt_map_arch() {
  case "${1:-$(uname -m)}" in
    x86_64|amd64)
      echo "x86_64"
      ;;
    aarch64|arm64)
      echo "aarch64"
      ;;
    armv7l|armv7)
      echo "armv7l"
      ;;
    *)
      echo "${1:-$(uname -m)}"
      ;;
  esac
}

dxrt_find_onnxlib_dirs() {
  local python_bin="${1:-python3}"
  "$python_bin" - <<'PY'
import glob
import importlib.util
import os
import site
import sysconfig

paths = []
seen = set()

def add_path(path: str) -> None:
    path = os.path.realpath(path)
    if not os.path.isdir(path) or path in seen:
        return
    try:
        names = os.listdir(path)
    except Exception:
        return
    if any("onnxruntime" in name.lower() for name in names):
        seen.add(path)
        paths.append(path)

spec = importlib.util.find_spec("onnxruntime")
if spec and spec.origin:
    root = os.path.dirname(os.path.realpath(spec.origin))
    for candidate in (
        root,
        os.path.join(root, "capi"),
        os.path.join(root, "libs"),
    ):
        add_path(candidate)

for base in site.getsitepackages() + [site.getusersitepackages()]:
    if not base:
        continue
    for candidate in glob.glob(os.path.join(base, "onnxruntime*")):
        add_path(candidate)
        add_path(os.path.join(candidate, "capi"))
        add_path(os.path.join(candidate, "libs"))

for candidate in (
    sysconfig.get_config_var("LIBDIR") or "",
    "/usr/lib",
    "/usr/local/lib",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/aarch64-linux-gnu",
    "/usr/lib/arm-linux-gnueabihf",
):
    if candidate:
        add_path(candidate)

print(":".join(paths))
PY
}

dxrt_sync_host_libs() {
  local dxrt_dir="$1"
  local host_lib_dir="$2"
  local src_lib="$dxrt_dir/lib/libdxrt.so"
  local dst_lib="$host_lib_dir/libdxrt.so"
  local src_real=""
  local dst_real=""

  mkdir -p "$host_lib_dir"
  if [[ -f "$src_lib" ]]; then
    src_real="$(readlink -f "$src_lib" 2>/dev/null || printf '%s' "$src_lib")"
    if [[ -e "$dst_lib" ]]; then
      dst_real="$(readlink -f "$dst_lib" 2>/dev/null || printf '%s' "$dst_lib")"
    fi
    if [[ -n "$dst_real" && "$src_real" == "$dst_real" ]]; then
      echo "[dxrt] host lib already points at $src_real; skipping copy"
    else
      cp -a "$src_lib" "$host_lib_dir/"
    fi
  else
    dxrt_fail "missing $src_lib"
  fi
  ldconfig || true
}

dxrt_install_service() {
  local dxrt_dir="$1"
  local host_lib_dir="$2"
  local unit="/etc/systemd/system/dxrt.service"
  local runtime_lib_path="$dxrt_dir/lib:$host_lib_dir:/usr/local/lib:/usr/lib:/lib"

  [[ -x "$dxrt_dir/bin/dxrtd" ]] || dxrt_fail "missing executable $dxrt_dir/bin/dxrtd"

  if command -v systemctl >/dev/null 2>&1; then
    cat >"$unit" <<EOF
[Unit]
Description=DXRT Runtime Service
After=network-online.target local-fs.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$dxrt_dir
ExecStart=$dxrt_dir/bin/dxrtd
Environment=LD_LIBRARY_PATH=$runtime_lib_path
Restart=always
RestartSec=2
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now dxrt.service
    systemctl restart dxrt.service
    return
  fi

  if pgrep -f "$dxrt_dir/bin/dxrtd" >/dev/null 2>&1; then
    dxrt_log "dxrtd already running without systemd"
    return
  fi

  dxrt_warn "systemd unavailable; starting dxrtd in background"
  env LD_LIBRARY_PATH="$runtime_lib_path" nohup "$dxrt_dir/bin/dxrtd" >/tmp/dxrtd.log 2>&1 &
  sleep 2
}

dxrt_validate_install() {
  local python_bin="$1"
  local dxrt_dir="$2"
  local host_lib_dir="$3"
  local dxrt_venv_dir="${4:-}"

  [[ -d "$dxrt_dir" ]] || dxrt_fail "missing DXRT directory: $dxrt_dir"
  [[ -f "$dxrt_dir/lib/libdxrt.so" ]] || dxrt_fail "missing DXRT library: $dxrt_dir/lib/libdxrt.so"
  [[ -x "$dxrt_dir/bin/dxrtd" ]] || dxrt_fail "missing DXRT daemon: $dxrt_dir/bin/dxrtd"

  dxrt_sync_host_libs "$dxrt_dir" "$host_lib_dir"
  dxrt_install_service "$dxrt_dir" "$host_lib_dir"

  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet dxrt.service || dxrt_fail "dxrt.service is not active"
  elif ! pgrep -f "$dxrt_dir/bin/dxrtd" >/dev/null 2>&1; then
    dxrt_fail "dxrtd process is not running"
  fi

  LD_LIBRARY_PATH="$host_lib_dir:${LD_LIBRARY_PATH:-}" "$python_bin" - <<'PY'
import pathlib
import dx_engine
print("[dxrt] dx_engine:", pathlib.Path(dx_engine.__file__).resolve())
print("[dxrt] dx_engine_version:", getattr(dx_engine, "__version__", "unknown"))
PY

  if [[ -n "$dxrt_venv_dir" && -x "$dxrt_venv_dir/bin/python" ]]; then
    LD_LIBRARY_PATH="$host_lib_dir:${LD_LIBRARY_PATH:-}" "$dxrt_venv_dir/bin/python" - <<'PY'
import pathlib
import dx_engine
print("[dxrt] venv_dx_engine:", pathlib.Path(dx_engine.__file__).resolve())
print("[dxrt] venv_dx_engine_version:", getattr(dx_engine, "__version__", "unknown"))
PY
  fi
}

dxrt_restart_runtime_containers() {
  local containers=(vms-dxnn-host-infer vms-api vms-event-recorder)
  local running=()
  local name

  command -v docker >/dev/null 2>&1 || return 0
  docker info >/dev/null 2>&1 || return 0

  for name in "${containers[@]}"; do
    if docker ps --format '{{.Names}}' | grep -Fxq "$name"; then
      running+=("$name")
    fi
  done

  if [[ ${#running[@]} -eq 0 ]]; then
    return 0
  fi

  dxrt_log "restarting containers to refresh DXRT bind mounts: ${running[*]}"
  docker restart "${running[@]}"
}

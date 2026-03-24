#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${ROOT_DIR}/scripts/linux/dxnn_host_infer_service.py"
DST_DIR="${DST_DIR:-${ROOT_DIR}/runtime/host/bin}"
DST="${DST_DIR}/dxnn_host_infer_service.py"
UNIT="${UNIT:-/etc/systemd/system/dxnn-host-infer.service}"
MODEL_DIR="${ROOT_DIR}/models"
DXRT_VENV_DIR="${DXRT_VENV_DIR:-${ROOT_DIR}/runtime/host/venv-dx-runtime}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
HOST_MODEL_MAP_FROM="${HOST_MODEL_MAP_FROM:-${MODEL_DIR}}"
HOST_MODEL_MAP_TO="${HOST_MODEL_MAP_TO:-${MODEL_DIR}}"
DXNN_CLASS_NAMES="${DXNN_CLASS_NAMES:-helmet,head,person}"
HOST_DXNN_BIND="${HOST_DXNN_BIND:-0.0.0.0}"
HOST_DXNN_PORT="${HOST_DXNN_PORT:-18081}"
HOST_LD_LIBRARY_PATH="${HOST_LD_LIBRARY_PATH:-${LD_LIBRARY_PATH:-}}"

echo "[dxnn-host] install/check start"

if [ ! -f "${SRC}" ]; then
  echo "[dxnn-host] source not found: ${SRC}" >&2
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends python3 python3-pip python3-opencv python3-numpy

if [ -x "${DXRT_VENV_DIR}/bin/python" ]; then
  PYTHON_BIN="${DXRT_VENV_DIR}/bin/python"
fi

mkdir -p "${DST_DIR}"
install -m 0755 "${SRC}" "${DST}"

cat > "${UNIT}" <<EOF
[Unit]
Description=DXNN Host Inference Service
After=network-online.target dxrt.service
Requires=dxrt.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=${PYTHON_BIN} ${DST}
Environment=HOST_DXNN_BIND=${HOST_DXNN_BIND}
Environment=HOST_DXNN_PORT=${HOST_DXNN_PORT}
Environment=HOST_MODEL_MAP_FROM=${HOST_MODEL_MAP_FROM}
Environment=HOST_MODEL_MAP_TO=${HOST_MODEL_MAP_TO}
Environment=DXNN_CLASS_NAMES=${DXNN_CLASS_NAMES}
Environment=LD_LIBRARY_PATH=${HOST_LD_LIBRARY_PATH}
Restart=always
RestartSec=2
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now dxnn-host-infer.service
systemctl restart dxnn-host-infer.service
sleep 1
systemctl --no-pager -l status dxnn-host-infer.service | sed -n '1,20p'
if command -v curl >/dev/null 2>&1; then
  curl -fsS "http://127.0.0.1:${HOST_DXNN_PORT}/healthz" || true
fi
echo "[dxnn-host] install/check done"

#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer currently supports Debian/Ubuntu hosts only." >&2
  exit 1
fi

. /etc/os-release
ARCH="$(dpkg --print-architecture)"
DISTRO_ID="${ID:-}"
CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"

case "$DISTRO_ID" in
  ubuntu)
    DOCKER_REPO_DISTRO="ubuntu"
    ;;
  debian)
    DOCKER_REPO_DISTRO="debian"
    ;;
  raspbian)
    DOCKER_REPO_DISTRO="raspbian"
    ;;
  *)
    echo "Unsupported distro for automatic Docker install: ${DISTRO_ID:-unknown}" >&2
    echo "Supported: ubuntu, debian, raspbian" >&2
    exit 1
    ;;
esac

if [[ -z "$CODENAME" ]]; then
  echo "Could not determine distro codename from /etc/os-release." >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release

install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL "https://download.docker.com/linux/${DOCKER_REPO_DISTRO}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi

echo \
  "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${DOCKER_REPO_DISTRO} ${CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker

echo "Docker installation complete."
echo "Optional: add your user to the docker group:"
echo "  sudo usermod -aG docker \$USER"
echo "Then log out and back in."

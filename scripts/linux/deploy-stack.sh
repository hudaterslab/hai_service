#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
COMPOSE_FILE="$REPO_ROOT/deploy/docker-compose.yml"
ENV_FILE="$REPO_ROOT/deploy/.env"
ENV_EXAMPLE="$REPO_ROOT/deploy/.env.example"
COMPOSE_PROJECT_NAME="${VMS_COMPOSE_PROJECT_NAME:-$(basename "$REPO_ROOT")}"
SKIP_BUILD="${VMS_SKIP_BUILD:-false}"
ALLOW_LOCAL_BUILD="${VMS_ALLOW_LOCAL_BUILD:-false}"

usage() {
  cat <<'EOF'
Usage:
  deploy-stack.sh init
  deploy-stack.sh up
  deploy-stack.sh down
  deploy-stack.sh restart
  deploy-stack.sh status
  deploy-stack.sh logs [service...]
  deploy-stack.sh ctl <vmsctl args...>
  deploy-stack.sh help

Commands:
  init     Create deploy/.env from .env.example if missing and create runtime directories.
  up       Start the full Docker stack. Local rebuilds require explicit opt-in.
  down     Stop the stack.
  restart  Restart the stack. Local rebuilds require explicit opt-in.
  status   Show container status.
  logs     Follow logs for selected services, default: vms-api vms-event-recorder vms-delivery-worker.
  ctl      Run the bundled Docker CLI wrapper, e.g.:
           deploy-stack.sh ctl monitor overview
           deploy-stack.sh ctl camera list
           deploy-stack.sh ctl destination check

Environment:
  VMS_COMPOSE_PROJECT_NAME   Override the Docker Compose project name.
  VMS_SKIP_BUILD=true        Skip image builds and start with existing images only.
  VMS_ALLOW_LOCAL_BUILD=true Explicitly allow local docker compose builds for up/restart.
EOF
}

compose() {
  docker compose --project-name "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

require_tools() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not installed. Run scripts/linux/install-docker.sh first." >&2
    return 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "docker compose plugin is not available." >&2
    return 1
  fi
}

init_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "Created $ENV_FILE from $ENV_EXAMPLE"
  else
    echo "$ENV_FILE already exists"
  fi

  local data_root
  data_root="$(awk -F= '$1=="VMS_DATA_ROOT"{print $2}' "$ENV_FILE" | tail -n 1)"
  if [[ -n "$data_root" ]]; then
    mkdir -p "$data_root/media" "$data_root/redis"
    echo "Ensured runtime dirs under $data_root"
  fi
}

wait_for_api() {
  local tries=60
  local i
  for ((i=1; i<=tries; i++)); do
    if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
      echo "vms-api is healthy"
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for vms-api health" >&2
  return 1
}

require_explicit_build_opt_in() {
  if [[ "$SKIP_BUILD" == "true" || "$ALLOW_LOCAL_BUILD" == "true" ]]; then
    return 0
  fi

  cat >&2 <<'EOF'
Refusing to run docker compose with --build by default.

Use one of:
  VMS_SKIP_BUILD=true ./scripts/linux/deploy-stack.sh up
  VMS_SKIP_BUILD=true ./scripts/linux/deploy-stack.sh restart

Or explicitly opt into a local rebuild:
  VMS_ALLOW_LOCAL_BUILD=true ./scripts/linux/deploy-stack.sh up
  VMS_ALLOW_LOCAL_BUILD=true ./scripts/linux/deploy-stack.sh restart
EOF
  return 1
}

ensure_vms_ops_image() {
  local image_ref="${COMPOSE_PROJECT_NAME}-vms-ops"
  if docker image inspect "$image_ref" >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$ALLOW_LOCAL_BUILD" == "true" ]]; then
    compose build vms-ops >/dev/null
    return 0
  fi

  cat >&2 <<EOF
Missing Docker image: $image_ref

Use one of:
  VMS_ALLOW_LOCAL_BUILD=true ./scripts/linux/deploy-stack.sh ctl <args...>
  sudo ./install.sh
  VMS_SKIP_BUILD=true ./scripts/linux/deploy-stack.sh up
EOF
  return 1
}

main() {
  local cmd="${1:-help}"
  case "$cmd" in
    init)
      require_tools
      init_env
      ;;
    up)
      require_tools
      init_env
      if [[ "$SKIP_BUILD" == "true" ]]; then
        compose up -d --no-build
      else
        require_explicit_build_opt_in
        compose up -d --build
      fi
      wait_for_api
      ;;
    down)
      require_tools
      compose down
      ;;
    restart)
      require_tools
      init_env
      if [[ "$SKIP_BUILD" == "true" ]]; then
        compose up -d --no-build
      else
        require_explicit_build_opt_in
        compose up -d --build
      fi
      wait_for_api
      ;;
    status)
      require_tools
      compose ps
      ;;
    logs)
      require_tools
      shift || true
      if [[ "$#" -eq 0 ]]; then
        set -- vms-api vms-event-recorder vms-delivery-worker
      fi
      compose logs -f "$@"
      ;;
    ctl)
      require_tools
      init_env
      shift || true
      ensure_vms_ops_image
      if [[ "$#" -eq 0 ]]; then
        compose run --rm vms-ops --help
      else
        compose run --rm vms-ops "$@"
      fi
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage >&2
      return 1
      ;;
  esac
}

main "$@"

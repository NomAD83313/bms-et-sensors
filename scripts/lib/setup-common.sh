#!/usr/bin/env bash

retry_cmd() {
  local label="$1"
  shift
  local attempt=1

  until "$@"; do
    if (( attempt >= RETRY_ATTEMPTS )); then
      echo "ERROR: ${label} failed after ${RETRY_ATTEMPTS} attempts." >&2
      return 1
    fi
    echo "WARN: ${label} failed (attempt ${attempt}/${RETRY_ATTEMPTS}). Retrying in ${RETRY_DELAY_SEC}s..."
    sleep "${RETRY_DELAY_SEC}"
    attempt=$((attempt + 1))
  done
}

install_prerequisites() {
  echo ">>> Installing base packages..."
  retry_cmd "apt-get update" sudo apt-get update
  retry_cmd "apt-get install base packages" sudo apt-get install -y ca-certificates curl git dnsmasq-base
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    echo ">>> Docker is already installed."
    return
  fi

  echo ">>> Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  sudo systemctl enable --now docker
  echo ">>> Docker installed. Re-login may be required for docker group."
}

resolve_docker_access() {
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
  else
    DOCKER_CMD=(sudo docker)
  fi
  COMPOSE_CMD=("${DOCKER_CMD[@]}" compose)
}

ensure_compose_plugin() {
  if "${COMPOSE_CMD[@]}" version >/dev/null 2>&1; then
    echo ">>> Docker Compose plugin is available."
    return
  fi

  echo ">>> Installing docker-compose-plugin..."
  retry_cmd "apt-get install docker-compose-plugin" sudo apt-get install -y docker-compose-plugin
}

disable_modemmanager_if_present() {
  if ! systemctl list-unit-files 2>/dev/null | grep -q "^ModemManager.service"; then
    echo ">>> ModemManager service not found. Skipping."
    return
  fi

  echo ">>> Disabling ModemManager (prevents USB serial capture on DAQ dongles)..."
  sudo systemctl stop ModemManager.service || true
  sudo systemctl disable ModemManager.service || true
  sudo systemctl mask ModemManager.service || true
}

disable_avahi_if_present() {
  if ! systemctl list-unit-files 2>/dev/null | grep -q "^avahi-daemon.service"; then
    echo ">>> avahi-daemon service not found. Skipping."
    return
  fi

  echo ">>> Disabling avahi-daemon (using NetworkManager local DNS alias instead)..."
  sudo systemctl disable --now avahi-daemon.service avahi-daemon.socket || true
  retry_cmd "apt-get remove avahi-daemon" sudo apt-get remove -y avahi-daemon libnss-mdns || true
}

prepare_repo() {
  if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo ">>> Cloning repository into $PROJECT_DIR"
    retry_cmd "git clone" git clone "$REPO_URL" "$PROJECT_DIR"
  fi

  cd "$PROJECT_DIR"
  echo ">>> Updating repository ($REPO_BRANCH)..."
  retry_cmd "git fetch" git fetch --all --tags --prune
  retry_cmd "git checkout ${REPO_BRANCH}" git checkout "$REPO_BRANCH"
  retry_cmd "git pull ${REPO_BRANCH}" git pull --ff-only origin "$REPO_BRANCH"
}

prepare_env_file() {
  if [ -f ".env" ]; then
    echo ">>> .env already exists."
    return
  fi

  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo "!!! .env created from .env.example"
    echo "!!! Edit .env before production use:"
    echo "    nano .env"
  else
    touch .env
    echo "!!! Empty .env created. Fill required variables before use."
  fi
}

read_env_var() {
  local key="$1"
  if [ ! -f .env ]; then
    return 1
  fi
  awk -F= -v k="$key" '$1==k{val=substr($0, index($0,"=")+1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", val); gsub(/^"|"$/, "", val); gsub(/^'\''|'\''$/, "", val); print val; exit}' .env
}

remove_stack_service() {
  local target="$1"
  local item
  local updated=()
  for item in "${STACK_SERVICES[@]}"; do
    if [ "$item" != "$target" ]; then
      updated+=("$item")
    fi
  done
  STACK_SERVICES=("${updated[@]}")
}

docker_container_exists() {
  local name="$1"
  local docker_cli=(docker)
  if declare -p DOCKER_CMD >/dev/null 2>&1; then
    # shellcheck disable=SC2154
    docker_cli=("${DOCKER_CMD[@]}")
  fi
  "${docker_cli[@]}" container inspect "$name" >/dev/null 2>&1
}

verify_stack_services_created() {
  local service
  local missing=()
  for service in "$@"; do
    if ! docker_container_exists "$service"; then
      missing+=("$service")
    fi
  done

  if [ "${#missing[@]}" -eq 0 ]; then
    echo ">>> Verified selected containers exist: $*"
    return 0
  fi

  echo "ERROR: selected services were not created: ${missing[*]}" >&2
  echo "ERROR: rerun the stack start and check the compose output:" >&2
  echo "       docker compose -f docker-compose.yml -f docker-compose.override.yml up -d ${missing[*]}" >&2
  return 1
}

run_pyrometers_setup() {
  local script_path="${SCRIPT_DIR}/pyrometers-setup.sh"
  if [ ! -f "$script_path" ]; then
    echo "WARN: ${script_path} not found. Skipping pyrometers setup." >&2
    return 0
  fi

  echo ">>> Running pyrometers USB setup (Micro-Epsilon / Optris)..."
  retry_cmd "pyrometers setup" sudo bash "$script_path" --apply
}

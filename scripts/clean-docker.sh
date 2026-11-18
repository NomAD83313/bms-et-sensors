#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project
if docker ps --format '{{.ID}}' >/dev/null 2>&1; then
  DOCKER_CMD=(docker)
else
  resolve_docker_access
fi
COMPOSE=("${DOCKER_CMD[@]}" "${COMPOSE[@]:1}")

compose_project_name() {
  "${COMPOSE[@]}" --profile '*' config --format json | python3 -c 'import json, sys; print(json.load(sys.stdin).get("name", ""))'
}

remove_compose_orphans() {
  local project_name service container_id services containers
  local -A active_services=()
  local orphan_ids=()

  if ! project_name="$(compose_project_name)" || [[ -z "${project_name}" ]]; then
    echo "ERROR: unable to resolve compose project; cleanup aborted" >&2
    return 1
  fi

  if ! services="$("${COMPOSE[@]}" --profile '*' config --services)" || [[ -z "$services" ]]; then
    echo "ERROR: unable to enumerate services; cleanup aborted" >&2
    return 1
  fi
  if ! containers="$("${DOCKER_CMD[@]}" ps -a \
      --filter "label=com.docker.compose.project=${project_name}" \
      --format '{{.ID}} {{.Label "com.docker.compose.service"}}')"; then
    echo "ERROR: unable to enumerate containers; cleanup aborted" >&2
    return 1
  fi

  while IFS= read -r service; do
    [[ -n "${service}" ]] && active_services["${service}"]=1
  done <<< "$services"

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    container_id="${line%% *}"
    service="${line#* }"
    if [[ -z "${active_services[${service}]+x}" ]]; then
      orphan_ids+=("${container_id}")
    fi
  done <<< "$containers"

  if ((${#orphan_ids[@]} == 0)); then
    echo ">> No compose orphan containers found"
    return
  fi

  echo ">> Removing compose orphan containers for project ${project_name}"
  "${DOCKER_CMD[@]}" rm -f "${orphan_ids[@]}" >/dev/null
}

echo "Cleaning Docker artifacts for THIS project only..."

if [[ "${1:-}" == "--orphans-only" ]]; then
  remove_compose_orphans
  echo "Project Docker orphan cleanup complete."
  exit 0
fi

remove_compose_orphans

echo ">> Stopping and removing project containers/networks/volumes/images (all profiles)"
"${COMPOSE[@]}" --profile '*' down --remove-orphans --volumes --rmi local

echo "Project Docker cleanup complete."

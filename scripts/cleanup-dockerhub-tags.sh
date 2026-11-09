#!/usr/bin/env bash
set -euo pipefail

# /gemini: Утилита очистки Docker Hub через API v2 с поддержкой фильтрации тегов и JWT-авторизации.
# Remove Docker Hub tags, keeping only selected ones.
#
# Required env:
#   DOCKERHUB_USER
#   DOCKERHUB_PASS      # password or Personal Access Token
#   DOCKERHUB_NS        # namespace, e.g. nomad375
#
# Optional env:
#   DOCKERHUB_REPOS     # comma-separated repo names (default: bms-mscl-collector,bms-redlab-collector,bms-graf-lite,bms-ap-control,bms-service-controller,bms-almemo-collector,bms-pyrometer-collector,bms-matter-collector)
#   KEEP_TAGS           # comma-separated keep list (default: latest)
#   DRY_RUN             # 1 to print actions only (default: 1)
#   PAGE_SIZE           # tags page size (default: 100)

: "${DOCKERHUB_USER:?Set DOCKERHUB_USER}"
: "${DOCKERHUB_PASS:?Set DOCKERHUB_PASS}"
: "${DOCKERHUB_NS:?Set DOCKERHUB_NS}"

DOCKERHUB_REPOS="${DOCKERHUB_REPOS:-bms-mscl-collector,bms-redlab-collector,bms-graf-lite,bms-ap-control,bms-service-controller,bms-almemo-collector,bms-pyrometer-collector,bms-matter-collector}"
KEEP_TAGS="${KEEP_TAGS:-latest}"
DRY_RUN="${DRY_RUN:-1}"
PAGE_SIZE="${PAGE_SIZE:-100}"

IFS=',' read -r -a REPOS <<< "${DOCKERHUB_REPOS}"
IFS=',' read -r -a KEEP <<< "${KEEP_TAGS}"

api_base="https://hub.docker.com/v2"

log() {
  printf '%s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "ERROR: required command not found: $1"
    exit 1
  fi
}

extract_json_string() {
  # Minimal extractor for a top-level string field in compact JSON.
  # Works with Docker Hub responses used in this script.
  local key="$1"
  sed -n "s/.*\"${key}\":\"\\([^\"]*\\)\".*/\\1/p"
}

extract_json_strings_by_key() {
  local key="$1"
  sed -n "s/.*\"${key}\":\"\\([^\"]*\\)\".*/\\1/p"
}

is_keep_tag() {
  local tag="$1"
  local k
  for k in "${KEEP[@]}"; do
    if [[ "$tag" == "$k" ]]; then
      return 0
    fi
  done
  return 1
}

delete_tag() {
  local repo="$1"
  local tag="$2"
  local url="${api_base}/repositories/${DOCKERHUB_NS}/${repo}/tags/${tag}/"
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY-RUN DELETE ${DOCKERHUB_NS}/${repo}:${tag}"
    return 0
  fi
  curl -fsS -X DELETE \
    -H "Authorization: JWT ${TOKEN}" \
    "$url" >/dev/null
  log "DELETED ${DOCKERHUB_NS}/${repo}:${tag}"
}

require_cmd curl
require_cmd sed

log ">>> Docker Hub cleanup start"
log "Namespace: ${DOCKERHUB_NS}"
log "Repos: ${DOCKERHUB_REPOS}"
log "Keep tags: ${KEEP_TAGS}"
log "DRY_RUN=${DRY_RUN}"

TOKEN="$(
  curl -fsS -X POST "${api_base}/users/login/" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${DOCKERHUB_USER}\",\"password\":\"${DOCKERHUB_PASS}\"}" \
  | extract_json_string token
)"

if [[ -z "${TOKEN}" ]]; then
  log "ERROR: failed to obtain Docker Hub JWT token."
  exit 1
fi

for repo in "${REPOS[@]}"; do
  repo="$(printf '%s' "$repo" | xargs)"
  if [[ -z "$repo" ]]; then
    continue
  fi

  log "=== ${DOCKERHUB_NS}/${repo} ==="
  next_url="${api_base}/repositories/${DOCKERHUB_NS}/${repo}/tags/?page_size=${PAGE_SIZE}"

  while [[ -n "${next_url}" && "${next_url}" != "null" ]]; do
    json="$(curl -fsS -H "Authorization: JWT ${TOKEN}" "${next_url}")"

    mapfile -t tags < <(printf '%s' "$json" | extract_json_strings_by_key name)
    if [[ "${#tags[@]}" -eq 0 ]]; then
      log "No tags found on current page."
    fi

    for tag in "${tags[@]}"; do
      if is_keep_tag "$tag"; then
        log "KEEP   ${DOCKERHUB_NS}/${repo}:${tag}"
      else
        delete_tag "$repo" "$tag"
      fi
    done

    next_url="$(
      printf '%s' "$json" \
      | extract_json_string next \
      | sed 's/\\u0026/\&/g'
    )"
    if [[ -z "${next_url}" ]]; then
      next_url="null"
    fi
  done
done

log ">>> Docker Hub cleanup done"

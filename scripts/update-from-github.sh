#!/usr/bin/env bash
# Update an existing installation to the current GitHub code and restart it.
#
#   ./scripts/update-from-github.sh --check   # report only: what would change, nothing is modified
#   ./scripts/update-from-github.sh           # back up, update the code, rebuild and restart
#   ./scripts/update-from-github.sh --no-restart   # back up and update the code only
#
# Safe for an installation made from an older version, including one whose local history
# no longer matches GitHub (the repository history was squashed). Before changing anything
# it saves .env, the current commit, local code changes (as a patch) and the container list
# under runtime/backups/update-<time>/. .env and runtime/ are never modified or removed.
# Camera services are restarted only when they were running before; nothing is restarted
# while a camera is learning.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project

BRANCH="${REPO_BRANCH:-main}"
CHECK_ONLY=0
RESTART=1
for argument in "$@"; do
  case "$argument" in
    --check) CHECK_ONLY=1 ;;
    --no-restart) RESTART=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; exit 1 ;;
  esac
done

say() { echo ">>> $*"; }
warn() { echo "WARNING: $*" >&2; }

# --- 1. Current state -----------------------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null
git remote get-url origin >/dev/null || { echo "No git remote 'origin'." >&2; exit 1; }
say "Fetching origin/${BRANCH}"
git fetch --prune origin "$BRANCH"
local_head="$(git rev-parse HEAD)"
remote_head="$(git rev-parse "origin/${BRANCH}")"
tracked_changes="$(git status --porcelain --untracked-files=no)"
untracked="$(git ls-files --others --exclude-standard)"
if [[ "$local_head" == "$remote_head" ]]; then
  update_mode=none
elif git merge-base --is-ancestor "$local_head" "$remote_head"; then
  update_mode=fast-forward
else
  # Local commits or a rewritten upstream history: the local code is replaced by GitHub's.
  update_mode=replace
fi
say "Local ${local_head:0:7} ($(git log -1 --format=%s HEAD)) -> GitHub ${remote_head:0:7} ($(git log -1 --format=%s "origin/${BRANCH}"))"
say "Update: ${update_mode}"
if [[ -n "$tracked_changes" ]]; then
  warn "local changes to tracked files (saved to the backup as a patch, then replaced by GitHub's version):"
  echo "$tracked_changes" | sed 's/^/    /'
fi
if [[ -n "$untracked" ]]; then
  say "Untracked files (kept; copied to the backup too):"
  echo "$untracked" | sed 's/^/    /'
fi

# Containers of this stack that run now; profile services are restarted only if they did.
running="$(docker ps --format '{{.Names}}' 2>/dev/null | sort || true)"
camera_running=0
grep -qxE 'camera-media|camera-analysis|camera-hub|camera-recorder|camera-dashboard|pi-camera-recorder|pi-camera-dashboard' <<<"$running" && camera_running=1
matter_running=0
thread_running=0
messkluppe_running=0
vibration_running=0
tunnel_running=0
grep -qxE 'matter-server|matter-collector' <<<"$running" && matter_running=1
grep -qx 'openthread-border-router' <<<"$running" && thread_running=1
grep -qx 'messkluppe-collector' <<<"$running" && messkluppe_running=1
grep -qx 'vibration-collector' <<<"$running" && vibration_running=1
grep -qx 'cloudflared' <<<"$running" && tunnel_running=1
[[ "$matter_running" == 1 ]] && say "Matter profile is running and will be restarted"
[[ "$thread_running" == 1 ]] && say "Thread profile is running and will be restarted"
[[ "$messkluppe_running" == 1 ]] && say "Messkluppe profile is running and will be rebuilt and restarted"
[[ "$vibration_running" == 1 ]] && say "Vibration profile is running and will be rebuilt and restarted"
[[ "$tunnel_running" == 1 ]] && say "Cloudflare Tunnel is running and will be restarted"

# .env keys: missing optional ones fall back to defaults; unused ones are harmless and kept.
required_keys="$(git show "origin/${BRANCH}:docker-compose.yml" | grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' | tr -d '${}' | sort -u)"
if [[ -f .env ]]; then
  env_keys="$(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d = | sort -u)"
  example_keys="$(git show "origin/${BRANCH}:.env.example" | grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' | tr -d = | sort -u)"
  # Variables Compose uses without a default (${NAME}, not ${NAME:-...}) must be set.
  missing="$(comm -13 <(echo "$env_keys") <(echo "$example_keys"))"
  missing_required="$(comm -12 <(echo "$missing") <(echo "$required_keys") | tr '\n' ' ')"
  missing_optional="$(comm -23 <(echo "$missing") <(echo "$required_keys") | tr '\n' ' ')"
  unused="$(comm -23 <(echo "$env_keys") <(echo "$example_keys") | tr '\n' ' ')"
  [[ -n "${missing_required// }" ]] && warn ".env lacks REQUIRED keys (set them before restarting): ${missing_required}"
  [[ -n "${missing_optional// }" ]] && say ".env lacks optional keys (defaults apply; copy from .env.example to change): ${missing_optional}"
  [[ -n "${unused// }" ]] && say ".env keys no longer in .env.example (ignored, kept): ${unused}"
else
  missing_required="$(echo "$required_keys" | tr '\n' ' ')"
  warn ".env is missing; create it from .env.example and set its secrets before restarting"
fi

# Learning must not be interrupted by a camera restart.
learning="$(sudo -n python3 - 2>/dev/null <<'PY' || true
import glob, json
for path in glob.glob('runtime/pi-camera/anomaly/status.json') + glob.glob('runtime/cameras*/**/anomaly/status.json', recursive=True):
    try:
        if json.load(open(path)).get('state') in ('learning', 'training'):
            print(path)
    except (OSError, ValueError):
        pass
PY
)"
if [[ "$camera_running" == 1 && -n "$learning" ]]; then
  warn "a camera is learning (${learning//$'\n'/, }); finish or pause learning, then run the update again"
  [[ "$CHECK_ONLY" == 1 ]] || exit 1
fi

if [[ -n "${missing_required:-}" && -n "${missing_required// }" && "$RESTART" == 1 && "$CHECK_ONLY" == 0 ]]; then
  echo "Add the required keys to .env (see .env.example), then run the update again." >&2
  exit 1
fi
if [[ "$CHECK_ONLY" == 1 ]]; then
  say "Check only: nothing was changed."
  exit 0
fi

# --- 2. Backup ------------------------------------------------------------------------
backup="runtime/backups/update-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
chmod 700 "$backup"
[[ -f .env ]] && install -m 600 .env "$backup/env"
{ echo "$local_head"; git log -1 --format='%ci %s' HEAD; } > "$backup/previous-commit.txt"
git diff HEAD > "$backup/local-changes.patch"
git ls-files --others --exclude-standard > "$backup/untracked-files.txt"
if [[ -s "$backup/untracked-files.txt" ]]; then
  tar -czf "$backup/untracked-files.tar.gz" -T "$backup/untracked-files.txt"
fi
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' > "$backup/containers.txt" 2>/dev/null || true
say "Backup: ${backup}"
say "Restore the previous code (local commits included) with: git reset --hard ${local_head:0:12}"

# --- 3. Code --------------------------------------------------------------------------
case "$update_mode" in
  none)
    # The commit already matches upstream, but the promised replacement still applies
    # to tracked local edits after their patch has been backed up.
    [[ -n "$tracked_changes" ]] && git reset --hard "origin/${BRANCH}"
    ;;
  fast-forward)
    if [[ -n "$tracked_changes" ]]; then
      git reset --hard "origin/${BRANCH}"
    else
      git merge --ff-only "origin/${BRANCH}"
    fi
    ;;
  replace) git reset --hard "origin/${BRANCH}" ;;
esac
# Always point the named local branch at the verified remote commit. This also handles
# an updater bootstrapped while HEAD was detached from an old local branch.
git checkout -q -B "$BRANCH" "origin/${BRANCH}"
git branch -q --set-upstream-to "origin/${BRANCH}" "$BRANCH" 2>/dev/null || true

if [[ "$RESTART" == 0 ]]; then
  say "Code updated; containers were not restarted (run ./scripts/restart-local.sh)."
  exit 0
fi

# --- 4. Containers --------------------------------------------------------------------
# The update can remove services (for example the former per-camera containers).
"${SCRIPT_DIR}/clean-docker.sh" --orphans-only
"${SCRIPT_DIR}/restart-local.sh"
if [[ "$camera_running" == 1 ]]; then
  "${SCRIPT_DIR}/restart-cameras.sh"
else
  say "Cameras were not running; start them with ./scripts/restart-cameras.sh"
fi
if [[ "$thread_running" == 1 ]]; then
  "${SCRIPT_DIR}/restart-openthread.sh"
fi
if [[ "$matter_running" == 1 ]]; then
  "${SCRIPT_DIR}/build-local-matter-app.sh"
  "${SCRIPT_DIR}/restart-matter-server.sh"
fi
if [[ "$messkluppe_running" == 1 ]]; then
  "${SCRIPT_DIR}/build-local-messkluppe.sh"
fi
if [[ "$vibration_running" == 1 ]]; then
  "${SCRIPT_DIR}/build-local-vibration.sh"
fi
if [[ "$tunnel_running" == 1 ]]; then
  "${SCRIPT_DIR}/restart-tunnel.sh"
fi

# --- 5. Checks ------------------------------------------------------------------------
say "Checks through the dashboard (HTTP status):"
port="$(read_env_var DASHBOARD_PORT 2>/dev/null || true)"
for path in /health.html /probe/svcctl /probe/influx /probe/grafana /probe/grafapp /api/control/cameras; do
  printf '    %-24s %s\n' "$path" "$(curl -s -o /dev/null -w '%{http_code}' -m 8 "http://127.0.0.1:${port:-80}${path}" || echo failed)"
done
say "Done. Open http://<host>/health.html; device probes show their hardware state."

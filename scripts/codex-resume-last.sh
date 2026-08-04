#!/usr/bin/env bash
set -euo pipefail

# Resume the latest local Codex session using the VS Code extension bundled CLI.
# This is a non-destructive recovery helper for cases where the VS Code UI does
# not show existing ~/.codex sessions after reconnect or window reload.

find_codex_bin() {
  local vscode_server_dir="${HOME}/.vscode-server/extensions"
  local candidate

  if [ ! -d "$vscode_server_dir" ]; then
    return 1
  fi

  while IFS= read -r candidate; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(
    find "$vscode_server_dir" \
      -path '*/openai.chatgpt-*/bin/linux-*/codex' \
      -type f \
      -printf '%T@ %p\n' 2>/dev/null \
      | sort -nr \
      | awk '{sub(/^[^ ]+ /, ""); print}'
  )

  return 1
}

CODEX_BIN="$(find_codex_bin || true)"

if [ -z "$CODEX_BIN" ]; then
  echo "ERROR: Codex CLI binary was not found under ~/.vscode-server/extensions/openai.chatgpt-*." >&2
  echo "ERROR: Open the OpenAI/ChatGPT VS Code extension on this host, then retry." >&2
  exit 1
fi

echo ">>> Using Codex CLI: ${CODEX_BIN}"
"$CODEX_BIN" --version
echo ">>> Resuming latest Codex session..."
exec "$CODEX_BIN" resume --last --all "$@"

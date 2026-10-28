#!/usr/bin/env bash
# PostToolUse(Write|Edit|NotebookEdit): record that THIS session wrote to the knowledge
# base (AGENTS.md, CLAUDE.md or docs/src/Agents). A per-session flag set from
# the agent's own tool call is exact, and safe with several agents in one repo.
# Contract: emit nothing, exit 0 always. This hook must never interrupt a turn.
set -u
payload="$(cat 2>/dev/null || true)"
case "$payload" in
  *docs/src/Agents*|*AGENTS.md*|*CLAUDE.md*) ;;
  *) exit 0 ;;
esac
sid="$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -z "$sid" ] && sid='unknown'
dir='.claude/.flush'
mkdir -p "$dir" 2>/dev/null || exit 0
: > "$dir/$sid.wrote" 2>/dev/null || true
find "$dir" -maxdepth 1 -type f -mtime +14 -delete 2>/dev/null || true
exit 0

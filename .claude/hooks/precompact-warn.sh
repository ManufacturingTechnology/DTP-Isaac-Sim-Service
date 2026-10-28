#!/usr/bin/env bash
# PreCompact: WARN (never block) when this session wrote nothing to the knowledge base
# (AGENTS.md, CLAUDE.md or docs/src/Agents). Warnings only, never blocking (ADR-0017).
# Contract: JSON on stdout, exit 0 always.
set -u
payload="$(cat 2>/dev/null || true)"
sid="$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -z "$sid" ] && sid='unknown'
[ -f ".claude/.flush/$sid.wrote" ] && exit 0
printf '%s\n' '{"systemMessage":"Knowledge-base check: this session has not written anything to AGENTS.md, CLAUDE.md or docs/src/Agents. Decisions, open questions, or research that exist only in this conversation will be summarized lossily by the compaction. Consider running the flush skill first (not blocked)."}'
exit 0

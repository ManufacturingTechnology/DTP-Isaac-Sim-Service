# CLAUDE.md

The repository's agent guidance is in `AGENTS.md` (shared by all coding agents), imported here:

@AGENTS.md

## Claude Code specifics

- **Skills** (`.claude/skills/`): run **`flush`** before ending a session or compacting; `garden` tidies the
  knowledge base; `diagnose` before reporting that something doesn't work.
- **Hooks** (`.claude/settings.json`): the content check runs before every Write/Edit/NotebookEdit and **blocks**
  a write that would put confidential or NVIDIA-owned material into a tracked file (AGENTS.md, "Vendor-confidential
  material stays out"); the knowledge-base hooks only warn, when the knowledge base looks stale or a session wrote
  nothing to it.
- **Memory:** personal reminders for the user go into Claude's auto memory (`~/.claude/projects/…/memory/`), never
  into repo files.

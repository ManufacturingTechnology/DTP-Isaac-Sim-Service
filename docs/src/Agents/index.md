---
title: 'Agent Docs'
audience: both
---

# Agent Docs

The agent knowledge base for this repository: technical knowledge, designs, decisions, and
research that agents (and people) accumulate while working here. `AGENTS.md` at the repo root is the
**hub** (map, rules, environment) for every coding agent; `CLAUDE.md` imports it and adds the Claude Code specifics
(skills, hooks, memory). The depth lives here and is read on demand.

!!! warning "Not published to end users"

    The default build (`make` in `docs/`, via `mkdocs.yml`) **excludes `Agents/`**. Only `make dev` (via
    `mkdocs.dev.yml`) includes it, appending an *Agent Docs* nav entry. `Agents/Reference/` is pruned even
    from the dev build: it is written for agents.

    **One-way links only.** Pages under `Agents/` may link to the user docs; **user docs must never link
    into `Agents/`** (the public build would have a broken link). The build hook
    (`docs/hooks/nav_merge.py`) warns on violations; `mkdocs build --strict` makes that fatal.

!!! info "What does NOT go here"

    - **Project management:** no backlog, ledger, priorities, owners, or sprints. Task tracking will
      live in GitHub Projects and issues.
    - **Personal reminders:** they apply to one person, so they live in that person's own Claude
      memory, not in the repo.
    - **An agent's own working plans** (step lists, session plans) stay local, in `~/.claude/plans/`
      or the session scratchpad. Shared technical plans are worked out in `Scratch/` and promoted into
      `Specs/` and `decisions.md` once settled (ADR-0017).

## Layout

| Path | Genre | Cadence | Audience |
|---|---|---|---|
| `AGENTS.md` (repo root) | **Hub:** map, routing, rules, environment, for every coding agent ([agents.md](https://agents.md/) convention) | slow; auto-loaded every session | agent |
| `CLAUDE.md` (repo root) | Imports `AGENTS.md` (`@AGENTS.md`) and adds the Claude Code specifics: skills, hooks, memory | rarely | agent |
| `Agents/Reference/` | **As-built:** how it works *now*, plus the load-bearing *why*; includes `decisions.md` and `glossary.md` | tracks the code | agent |
| `Agents/Specs/` | **Leading technical design:** formats, protocols, internals the code will follow (ADR-0019) | per design change | both |
| `Agents/Research/` | **Finished research:** dated, cited reports promoted out of `Scratch/` when complete; immutable (corrections go in a new report) | per promotion | both |
| `Agents/Scratch/` | **The user's local playground, gitignored on purpose** (ADR-0017): exploration, drafts, plans, harnesses, and research in progress, by topic. Settled results are promoted into the folders above; no tracked index lists its files | churns | both |

Cross-cutting, single-authority files:
- `Reference/decisions.md`: the decisions log (ADRs). **Read it before reversing a deliberate design.**
- `Reference/glossary.md`: overloaded terms. The user-facing terminology is in the user docs
  ("Control Terminology").

!!! info "Personal vs project instructions"

    `~/.claude/CLAUDE.md` (or another agent's user-level file) is **personal** and applies to every project its
    owner opens. The repo's `AGENTS.md` is **project** scope, travels via git, and is about *how to work on this
    repo*. Test for the repo file: "would a new contributor need this to avoid breaking something?"

## Conventions

- **Three-tier index.**
  - Tier 1 is the one-line map in `AGENTS.md`.
  - Tier 2 is this page.
  - Tier 3 is a 2–4 sentence **abstract at the top of every `Reference/` and `Specs/` page and every plan
    file**, ending with "Verify against the code; may lag."
  - Tiers 1 and 2 are purpose-level and rarely change; only tier 3 tracks content.
- **Update the abstract on edit.** When you add, remove, or re-scope a file, update this page's index
  **and** the `AGENTS.md` map.
- **Front matter on every page:** `title`, `audience` (`agent` or `both`; default `agent` in
  `Reference/`, `both` elsewhere). Dated research reports (`Scratch/<topic>/YYYY-MM-DD_*.md`) also carry `date`, `sources` (or a "Sources"
  section), `method`, and `status`.
- **Folder names are navigation labels** (the `include_dir_to_nav` plugin uses them verbatim), so they
  are capitalized: `Reference/`, `Specs/`, `Research/`, `Scratch/` (topic folders inside `Scratch/` are
  lowercase). New research starts in `Scratch/<topic>/` and is promoted to `Research/` when finished.
- **Repo-relative paths only (privacy).** No absolute paths, usernames, or machine-specific locations
  in any page, harness, or saved tool output; refer to other repositories by name and to per-user
  locations generically (`~/.claude/`, "the session scratchpad"). Redact before saving. This is the one
  permitted edit to an otherwise immutable research report; add a front-matter `note:` saying so. The
  docs build warns on absolute paths, and `flush`/`garden` grep for them (command in `AGENTS.md` Rules).
- **Cite code by concept anchor** (module, class, function, section), not by line number: files get
  edited, split, and re-folded.
- **Cite sources.** Mark claims verified in the source with V and unverified ones with ❓. A probe
  (code you ran) is a source; name what you ran and what it returned.
- **Docs lead the code (ADR-0019).** The user docs (the user's projected experience) and `Specs/` (the
  projected technical design) are written ahead of the code, using decided names as if they had landed;
  content ahead of the code carries a `!!! info "Planned"` marker. When code lands, correct the docs to
  what was built unless they have moved further ahead. `Reference/` stays as-built.
- **Living documentation.** After any non-trivial code change, update the matching `Reference/` page
  (and `decisions.md` if a decision changed) **in the same turn**, so the knowledge survives the
  session.
- **Decisions.** If a change conflicts with an entry in `decisions.md`, surface the conflict and the
  entry's "Revisit if" condition rather than silently overriding or complying.
- **Diátaxis applies to the user docs, not this tree.** This tree is organized by its relationship to
  the code (tracks it, leads it, explores). Skills (`.claude/skills/*/SKILL.md`) are how-to procedures.

## Tooling

- **Skills** (`.claude/skills/`):
  - `flush`: before ending a session or compacting, write the session's durable knowledge here.
  - `garden`: periodic tidy with per-folder edit rights.
  - `diagnose`: this repo's debugging procedure.
- **Hooks** (`.claude/settings.json`), **warnings only**:
  - on Stop, warns when code is newer than every knowledge-base doc;
  - before compacting, warns if nothing was written to the knowledge base this session.

## Index

**`Reference/`** (as-built):

- `decisions.md`: the decisions log (ADRs) and the open questions.
- `glossary.md`: overloaded terms (component, segment, state, process, command…).
- `control-apis.md`: the control stack as built (`AutomationComponent`, `CoreApi` / `SegmentEngine` + `SegmentApi`,
  frames, generated clients, configuration) and its verification status.
- `robots.md`: the RobotAPI, the kinematics service (ADR-0010, port 8014), the KinematicsSolver, the
  MotionPlanner, execution semantics, end-effector combinations, cuMotion, tool changers, robot conventions and
  findings.
- `model-editor.md`: the pydantic form editor as built (matching, validation routing, list editing, the
  example window, offline tests).
- `spacemouse.md`: the native SpaceMouse support (the lib's dependency-free HID reader and `SpaceMouse` class,
  the Windows and Linux backends, the extension's camera backend and axis mapping, tested devices, srl removal).
- `threading-model.md`: **the threading rule**: APIs run on their own thread and schedule only Isaac-touching
  calls on Isaac's main loop (the kinematics service is the exception).
- `dev-environment.md`: portable setup and commands (Isaac Sim Python, offline USD/cuMotion, tests,
  harnesses, docs builds).

**`Specs/`** (leading technical design): see `Specs/index.md`.

**`Research/`**: finished, promoted research; see `Research/index.md` (none promoted yet).

**`Scratch/`**: local and gitignored, so not indexed here (see the Layout table).

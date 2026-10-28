---
name: diagnose
description: Systematically diagnose something that isn't working before reporting inability — a failing command, test, build, import, Isaac Sim behavior, or a library that seems not to support something. Inventories the tools available in this repo's environment, reads the real documentation and library source, isolates the failing layer in a sandbox (offline USD stage, harness, probe script), classifies the cause, and records versions and repro steps.
---

# Diagnose before concluding

An error message names **where execution stopped**, not **why**.

## Do this, in order

1. **Inventory the environment.** Name the tools before guessing at causes:
   - Isaac Sim's Python and its bundled packages (`extension/isaac-sim/...`; see
     `docs/src/Agents/Reference/dev-environment.md`);
   - the offline USD stage (`pxr` from `omni.usd.libs`);
   - the offline harnesses (`docs/src/Agents/Scratch/harnesses/`);
   - the lib test suite;
   - the Isaac Sim logs (the Kit log folder, `%USERPROFILE%\.nvidia-omniverse\logs` on Windows ❓;
     confirm the location);
   - WSL for Linux-only tools; the browser pane for web UIs and Swagger.
2. **Read the real documentation and source:** vendor docs, the installed package's own source (Isaac
   Sim extensions are plain Python under `extension/isaac-sim/exts` and `extscache`), schema files,
   release notes. Do this before probing: it is cheaper.
3. **Isolate the failing layer** with the smallest test, in a **sandbox** (a scratch script, an offline
   USD stage, a harness, a temp venv). A probe that can't damage anything can be aggressive, and a fresh
   environment separates "broken here" from "broken everywhere".
4. **Classify the cause:**

   | Class | Fix | Note |
   |---|---|---|
   | Missing dependency | install it | a missing library is **not** a limitation |
   | Wrong or missing configuration | a flag, env var, path, or setting | |
   | Conflict between existing design decisions | resolve the conflict | the class most often misdiagnosed as a technical limit; check `docs/src/Agents/Reference/decisions.md` |
   | Genuine technical limit | route around it | only after excluding the three above |

5. **Separate the method failing from the goal failing.** If the method failed, find another route to
   the same goal.
6. **Record it** where it belongs (the matching `Reference/` page, or a dated report in the topic folder
   under `docs/src/Agents/Scratch/` for a substantial investigation): the verbatim symptom, versions, OS and platform, the exact repro command,
   **every configuration tried and what each produced**, the root cause or remaining unknown, and the
   date.
7. **Stop after four consecutive attempts that reveal nothing new,** then report.

## Reporting contract

Report what was tried and what each attempt revealed, the root cause or the specific blocking unknown,
two or more options with tradeoffs, and a recommendation. Never just "I couldn't."

## Guardrails

- Finding an option isn't permission to take it: `AGENTS.md`'s ask-first list decides.
- Don't suppress an error or widen a config to make a symptom disappear without naming the root cause.
- When a probe contradicts the docs, correct the docs.

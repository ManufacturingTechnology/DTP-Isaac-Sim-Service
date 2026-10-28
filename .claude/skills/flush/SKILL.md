---
name: flush
description: Write everything this session knows but has not written down into the repo's knowledge base (docs/src/Agents). Sweep the conversation for decisions, corrections, open questions, blocked items, new invariants, and research results; record decisions in Reference/decisions.md, open technical questions in the topic plan (Scratch/<topic>/plan-*.md) or Specs/, research in Scratch/<topic>/; sync Reference/*.md if code changed. Use BEFORE ENDING A SESSION, before /compact, after a dense design stretch, or whenever knowledge exists only in chat.
---

# Flush the session's knowledge to disk

**The premise:** the transcript is not durable memory; `docs/src/Agents/` is. Anything decided in conversation
but never written down is gone when the session ends or is compacted. This repo deliberately has **no
project-management ledger**: knowledge goes into the genre that fits it (see `docs/src/Agents/index.md`).

## When

- Before ending or closing a session (nothing warns you; the window just closes).
- Before `/compact` (a pre-compact hook warns if this session wrote nothing to the knowledge base).
- After a dense stretch: a design discussion, a diagnosis with dead ends, a pile of user decisions.

Running it too often costs a few minutes; running it too rarely loses knowledge that can't be
reconstructed.

## Do this

1. **Sweep the session, not the diff.** Extract every item in these categories:
   - **Decisions:** what was settled, with the *why*.
   - **Corrections:** a claim the user corrected, or one you found wrong. These are the highest value,
     because a summary tends to keep the wrong version.
   - **Open questions:** asked and **not answered**.
   - **Blocked items,** and what would unblock them.
   - **New invariants and gotchas,** found the hard way.
   - **Research results** with their sources (V / ❓).
2. **Write each to its home:**
   - decisions → `docs/src/Agents/Reference/decisions.md`: a new ADR, or supersede an old one (never
     delete);
   - open technical questions, design changes, phasing → the relevant plan, `docs/src/Agents/Scratch/<topic>/plan-*.md`
     (deferred or planned design also goes into `docs/src/Agents/Specs/`, marked, so it is tracked by git);
   - new research → a new dated report `YYYY-MM-DD_<slug>.md` in the matching topic folder under
     `docs/src/Agents/Scratch/` (an existing topic,
     or a new topic folder; synthesized results at the topic's top level, raw or underlying research in
     its `supporting/` subfolder; front matter: `date`, `method`, sources, `status`), and its row in that
     folder's `index.md`. Finished research is promoted to `docs/src/Agents/Research/` only when the user
     says it's ready;
   - as-built facts about code that changed → the matching `docs/src/Agents/Reference/*.md`;
   - user-facing knowledge → the user docs (`docs/src/` outside `Agents/`), never linking into `Agents/`;
   - **personal reminders** for the user (todos, "do X later") → **the user's own Claude memory, never
     a repo file.**
3. **Sync `Reference/` only if code changed.** Verify with
   `git status --porcelain lib extension coprocessor`; if nothing changed, say so.
4. **Update the indexes if the file set changed:** `docs/src/Agents/index.md`, the `AGENTS.md` map, and the
   file's own abstract.
5. **Validate:**
   - fenced code blocks balanced;
   - tables not ragged;
   - relative `.md` links resolve;
   - no invented dates;
   - user docs don't link into `Agents/` (the docs build warns);
   - **no absolute paths or usernames** in anything written (privacy; `AGENTS.md` Rules). This should
     print nothing:

     ```bash
     git grep -nIE '[A-Za-z]:[\\/][Uu]sers[\\/]|/(home|Users)/[^/ ]+/|/mnt/[a-z]/[Uu]sers/' -- .
     ```
   - **confidentiality sweep** (ADR-0026): nothing from a private source reached a tracked file beyond a
     generalized conclusion (no command names, message formats, part numbers, document titles, quotes);
     research drawing on private sources is marked `confidential: true` and stays in Scratch; standards are cited
     with attribution and not reproduced; no NVIDIA asset bytes or NVIDIA source were copied in. Then run
     `python3 .github/scripts/check-content.py`; it should pass.
6. **Report** what was recorded and what you chose not to record, and why. Then say the session's
   knowledge is on disk.

## Guardrails

- Don't fabricate a clean bill of health; say plainly if something was skipped.
- Condense, never discard load-bearing rationale.
- Mark low-confidence decisions as such.
- **Don't commit**: flushing is not a git operation.

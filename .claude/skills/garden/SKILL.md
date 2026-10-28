---
name: garden
description: Tidy the agent knowledge base under docs/src/Agents and the AGENTS.md hub — refresh stale tier-3 abstracts, distill changelog narrative out of Reference docs, fold superseded notes into the prose, verify cross-references and the one-way link rule, keep indexes matching the folders, and keep AGENTS.md and CLAUDE.md lean. Use when asked to garden or tidy the docs, or when the knowledge base has drifted.
---

# Garden the knowledge base

Read `docs/src/Agents/index.md` first: it defines the genres, the three-tier index, and the conventions. The
pass is **read-heavy and curation-minded**: condense and reconcile, but *propose* (don't silently make)
big structural merges or splits.

## Scope: what gardening may touch

| Target | Gardening may… | Why |
|---|---|---|
| `docs/src/Agents/Reference/*.md` | **edit freely**: distill narrative out, refresh abstracts, re-fold | mirrors the code; no authorial voice at stake |
| `docs/src/Agents/Reference/decisions.md` | **edit the format only; never delete an entry**; reversed decisions are *superseded* | the decision history is the point |
| `docs/src/Agents/index.md`, `Research/index.md`, `Scratch/index.md` and each topic's `index.md` | **edit**: keep the indexes matching the folders | maps |
| `AGENTS.md` (and the Claude-specific `CLAUDE.md`, which imports it) | **edit**: keep them lean, fix the map | it's a hub; drift is the failure mode |
| `docs/src/Agents/Scratch/<topic>/plan-*.md` | **light touch**: abstracts, broken references, status lines; don't change designs | shared technical plans, authored deliberately |
| `docs/src/Agents/Specs/*.md` | **light touch only**: abstract, references, formatting | contracts; propose, don't apply |
| `docs/src/Agents/Scratch/**` (drafts, harnesses) | **do not edit; report only** | in-progress thinking |
| `docs/src/Agents/Research/20*.md`, `docs/src/Agents/Scratch/**/20*.md` (dated research reports) | **never edit**: reports are immutable; corrections go in a new report | dated evidence |
| User docs (`docs/src/` outside `Agents/`) | **do not edit** | a different audience and structure (Diátaxis) |

**The one cross-tree rule to enforce:** user docs must never link into `Agents/`. Check with:

```bash
grep -rn '](\([^)]*/\)\?Agents/' docs/src --include='*.md' --exclude-dir=AI | grep -v '`'
```

**The privacy rule to enforce:** tracked files contain only repo-relative paths, never absolute paths or
usernames. This should print nothing; redact any hit (in a dated research report too: that is the one
permitted edit, recorded with a front-matter `note:`). Testbed USD hits are reported to the user, not
edited.

```bash
git grep -nIE '[A-Za-z]:[\\/][Uu]sers[\\/]|/(home|Users)/[^/ ]+/|/mnt/[a-z]/[Uu]sers/' -- .
```

## Checklist

1. **Orient:** read `docs/src/Agents/index.md` and `docs/src/Agents/Scratch/index.md`.
2. **Distill narrative out of `Reference/`.** Keep current state plus the load-bearing *why*; move
   session-by-session history to the topic's plan in `Scratch/<topic>/` (or drop it if obsolete).
3. **Reconcile superseded prose:** fold corrections into the body.
4. **Refresh the tier-3 abstracts** (Reference, Specs, plans); spot-check claims against the code.
5. **Verify cross-references:** every referenced file exists; `AGENTS.md`'s map lists the actual files;
   no doc or code comment points at a moved file; no absolute paths (the grep above).
6. **Confidentiality sweep before promoting anything out of Scratch** (ADR-0026): a Scratch file marked
   `confidential: true`, or one citing a private source, is promoted only after a sanitizing pass the user
   confirms (generalized conclusions only; no names, formats, part numbers, titles, quotes). Run
   `python3 .github/scripts/check-content.py`; it should pass.
7. **Check the hub stayed lean:** `wc -l AGENTS.md CLAUDE.md`, a few hundred lines at most.
8. **Report** what changed, and propose structural merges or splits for the user to approve.

## Guardrails

- Condense, never blindly discard.
- Don't invent dates.
- Structural changes update `docs/src/Agents/index.md` and the `AGENTS.md` map in the same pass.

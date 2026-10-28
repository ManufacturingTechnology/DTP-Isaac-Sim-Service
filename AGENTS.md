# AGENTS.md

Guidance for AI coding agents (and people) working in this repository: the **AMT DTP Isaac Sim Service** (ISS),
an NVIDIA Isaac Sim extension plus libraries for simulating manufacturing cells (machines, robots,
workpieces) and controlling them over web APIs. This file is the **hub**: map, rules, environment. The
depth lives in the agent knowledge base, `docs/src/Agents/` (start at `docs/src/Agents/index.md`).

## Map

| Path | What |
|---|---|
| `lib/` | `amt-dtp-ise-lib`: pure Python (no Isaac Sim dependency). Control models, the control APIs (`lib/src/amt/dtp/ise/lib/control/`: `ApiHost` (`host.py`, the server-on-its-own-thread base of every API), `AutomationComponentBase`, `CoreApi`, `SegmentEngine` + `SegmentApi`, clients, codegen, frame merging; the kinematics service `KinematicsApi` (an `ApiHost`, not a `CoreApi`: uploaded solvers, neutral obstacles, no Isaac scene; the extension serves it on port 8014) and `RobotApi` with pose models, trajectory compiling, the `MotionPlanner` interface, and end-effector fragment merging), logging, and `devices.spacemouse` (a dependency-free HID SpaceMouse reader any app can use). Tests in `lib/tests/`. |
| `extension/` | The Isaac Sim extension `amt.dtp.ise` (`extension/exts/amt.dtp.ise/`): transducers (`transducer/`), behavior scripts (`scripting.py`), entities, MTConnect (`iiot/`), UI, API routers. In dev, `lib` is symlinked into it. |
| `coprocessor/` | The Isaac Sim Co-Processor (ISCP): a separate application, launched and managed by the extension, for capabilities whose Python packages conflict with Isaac Sim's environment; `lib` has its API client. |
| `container/` | Container deployment (Docker Compose, Linux). |
| `docs/` | MkDocs user docs (`docs/src/`) **plus** the agent knowledge base (`docs/src/Agents/`, excluded from the public build). `docs/src/Agents/Scratch/` is a local, gitignored playground (see Rules). |
| `.claude/` | Claude Code machinery: `settings.json` (hooks), `skills/` (`flush`, `garden`, `diagnose`; their `SKILL.md` files are plain checklists any agent can follow), `hooks/`. Machine-local files are gitignored. `CLAUDE.md` imports this file and adds the Claude-specific parts. |

Testbeds and demo assets live in a separate repository, `DTP-Isaac-Sim-Service-Alpha-Testbeds` (ADR-0018).

## Routing: read before working on…

| Task | Read |
|---|---|
| anything non-trivial | `docs/src/Agents/index.md`, then `docs/src/Agents/Reference/decisions.md` |
| control APIs, automation components, segments, frames, generated clients | `docs/src/Agents/Reference/control-apis.md`, `docs/src/Agents/Specs/segment-protocol.md`; user docs `docs/src/Asset Development/050-Segments-and-Control-APIs.md` |
| robots, cuMotion, tool changers, kinematics, the kinematics service (port 8014) | `docs/src/Agents/Reference/robots.md`, `docs/src/Agents/Specs/robot-control.md`, `docs/src/Agents/Specs/end-effector-package.md`; user docs `docs/src/Asset Development/070-Manipulator-Robots.md` |
| interoperability (industrial robot controllers, ROS 2, fieldbuses, OPC UA), clock ownership, timing modes | `docs/src/Agents/Specs/segment-protocol.md` "Planned (not built)"; user docs `docs/src/Asset Development/080-Interoperability.md` |
| user docs structure | `docs/src/Agents/Reference/decisions.md` (ADR-0019: docs lead the code), `docs/src/Asset Development/` |
| the model editor UI (config / custom-data forms) | `docs/src/Agents/Reference/model-editor.md` |
| the SpaceMouse, HID input devices, camera navigation | `docs/src/Agents/Reference/spacemouse.md`; user docs `docs/src/Getting Started/020-Install.md` "SpaceMouse" |
| new APIs, servers, threads, anything that calls Isaac Sim from outside a callback | `docs/src/Agents/Reference/threading-model.md` |
| setting up, running tests or docs | `docs/src/Agents/Reference/dev-environment.md` |
| terms that seem overloaded | `docs/src/Agents/Reference/glossary.md` |

## Rules

- **The knowledge base is durable memory; the chat is not.** After any non-trivial code change, update the
  matching `docs/src/Agents/Reference/` page (and `decisions.md` if a decision changed) **in the same turn**.
  Before ending a session or compacting your context, write down everything the session decided or learned that
  isn't on disk yet (decisions, corrections, open questions, research; the checklist is
  `.claude/skills/flush/SKILL.md`, which Claude Code runs as the `flush` skill).
- **Cite sources** for claims that come from outside the repo (standards, vendor docs, library behavior),
  in the artifact itself. Mark claims verified in the source V, unverified ❓; a probe (code you ran) is
  a source.
- **Docs lead the code** (ADR-0019): the user docs (projected user experience) and `docs/src/Agents/Specs/`
  (projected technical design) are written ahead of the code with decided names, and drive
  implementation; when code lands, correct them to what was built unless they have moved further ahead.
  `docs/src/Agents/Reference/` stays as-built.
- **Threading: keep Isaac Sim's event loop fast.** Isaac Sim's main thread runs the event loop that drives
  rendering, the timeline, physics callbacks, and behavior scripts, and many Isaac libraries must be called on
  it. Every API is an `ApiHost` (lib `control/host.py`): its HTTP server runs **on its own thread**, and it
  schedules **only** the Isaac-touching calls as short coroutines on **Isaac's main loop**
  (`run_coro_on_main_loop`; the ISE API's alias `run_coro_on_isaac_loop`); everything else stays off the loop. The kinematics service is the exception (it touches no Isaac state). Details:
  `docs/src/Agents/Reference/threading-model.md`.
- **One-way links:** user docs (`docs/src/` outside `Agents/`) must never link into `docs/src/Agents/`.
- **No project management in the repo:** no backlogs, ledgers, priorities, or owners (GitHub Projects and
  issues later). **Personal reminders** belong in that person's own agent memory, never in repo files.
  Agents' own working plans stay local (the agent's plan folder or scratchpad).
- **`docs/src/Agents/Scratch/` is the user's local playground, gitignored on purpose** (ADR-0017). It holds work in
  progress (exploration, drafts, plans, harnesses, research) worked out with the user before anything is
  committed. Settled results are **promoted** out of it into tracked files: decisions into
  `Reference/decisions.md`, designs into `Specs/`, as-built facts into `Reference/`, finished research into
  `Research/`, user-facing knowledge into the user docs. Because it is local and temporary, **don't list Scratch
  files in this file, `CLAUDE.md`, or the tracked indexes**. A tracked doc may cite a Scratch file as its source, but must
  stand on its own without it: a clone has no `Scratch/`. Agents may read and write there freely.
- **Vendor-confidential material stays out** (ADR-0026). Confidential or proprietary information never enters a
  tracked file: vendor manuals and specifications that aren't public (e.g. robot controller interface manuals),
  anything covered by a nondisclosure agreement, customer or partner information, internal business information,
  personal data, credentials, unreleased product details. For every source you read, decide **public or
  private**; when unsure, treat it as private and ask the user.
  - Private sources may inform the design, but tracked files carry only **generalized conclusions**
    (capabilities, limitations, impact on our interfaces): no command names, message formats, register or part
    numbers, document titles or file names, and no quotes.
  - Research that draws on private sources stays in `docs/src/Agents/Scratch/` with `confidential: true` in its
    front matter, and is promoted only after a sanitizing pass the user confirms.
  - **Standards:** cite every standard you use (body, number, edition or year, clause, title, link) and give its
    publisher attribution. Paywalled standards (ISO, IEC, ASME, …) are summarized in our own words; quote at most a
    short phrase, never tables, figures, or clauses. Open specifications follow their own license's attribution
    terms.
  - **Enforcement:** `.github/scripts/check-content.py` scans for confidentiality markings, locally denylisted
    terms, and NVIDIA-owned content. `make` and CI run it on every file git would commit, the opt-in pre-commit hook
    on staged files, and in Claude Code a PreToolUse hook blocks any Write/Edit that would put a match into a
    tracked file, here or in a sibling repository. If it flags your change, don't work around it: rewrite, move it
    to Scratch, or ask the user. The terms list is local and never committed (`.claude/confidential-terms.txt`,
    `~/.claude/confidential-terms.txt`).
- **NVIDIA material is referenced, never included** (ADR-0025). Isaac Sim, Kit, cuMotion, Lula, their extensions,
  and NVIDIA's assets (robots, props, environments, MDL materials, textures, HDRIs, sample scenes, from the asset
  server or the install) are not ours to redistribute. A USD reference or payload to an asset-root path, or
  `@OmniPBR.mdl@` resolved from the user's install, is fine; copying the bytes into this repo or the testbeds
  repository, flattening or exporting a stage that pulls them in, or pasting NVIDIA source with its header is not.
  Before adding any asset, check its composition arcs and asset-valued attributes for NVIDIA or vendor content;
  for NVIDIA code under Apache-2.0 (Isaac Sim's own source), keep its header and list it in the subproject's
  `THIRD-PARTY-NOTICES.md`, and tell the user first.
- **Privacy and portability: repo-relative paths only.** Never write an absolute path, a username, a home
  folder, or any other machine-specific location into a tracked file (docs, research reports, code
  comments, configs, harnesses). Use repo-relative paths (e.g. the `extension/isaac-sim` symlink to the
  Isaac Sim install), `${workspaceFolder}`-style variables, or a path computed from `__file__`. Refer to
  other repositories **by name only**, and to per-user locations generically (`~/.claude/`, "the session
  scratchpad"). Redact tool output (logs, tracebacks, probe output) before saving it in the repo.
  Check before finishing; this should print nothing:
  `git grep -nIE '[A-Za-z]:[\\/][Uu]sers[\\/]|/(home|Users)/[^/ ]+/|/mnt/[a-z]/[Uu]sers/' -- .`

## Ask first (in addition to your global defaults)

Stop and ask before:

- contradicting an entry in `docs/src/Agents/Reference/decisions.md`;
- **editing testbed or scene USD files** (they are edited by the user; describe the change needed instead);
- adding a dependency (Python packages, Isaac Sim extensions, docs plugins, JavaScript, vendored or copied code,
  assets, models, fonts). **Check its license first, including everything it pulls in or bundles**: for a Python
  package run `python3 .github/scripts/check-licenses.py <package>` (it resolves the full dependency closure; the
  same policy `make` enforces), and read the package's own license files for libraries its wheel bundles (metadata
  can understate them). If anything isn't permissive (GPL, AGPL, LGPL, SSPL, MPL, EPL, non-commercial, proprietary,
  or unknown), **tell the user before adding it**: the license, what it would mean for what we ship, and the
  alternatives (an optional extra, another package). ADR-0025. The same applies to an asset or model added to the
  testbeds repository (its `AGENTS.md`);
- changing a stored format, a wire format, or a model schema in a way that changes generated-client
  fingerprints, unless the current task is exactly that;
- deleting files you didn't create in this session, or rewriting git history;
- committing, pushing, or opening PRs (unless asked).

These add to the global "finding an option is not permission to take it" defaults; they don't replace them.

## Environment (summary)

Details and commands: `docs/src/Agents/Reference/dev-environment.md`.

- Isaac Sim is reached through the **`extension/isaac-sim` symlink** (gitignored; create it per checkout).
- Lib tests: `lib/test.bat` (Windows) or `make -C lib test` (Linux); quick loop
  `PYTHONPATH=lib/src pytest lib/tests` in a venv with `pytest fastapi uvicorn requests`.
- Offline (no Isaac Sim running): `pxr` from Isaac Sim's bundled USD libraries; offline harnesses, where a
  checkout has them, are local in `Scratch/`.
- Docs: `cd docs && make` (public) or `make dev` (with `Agents/`; `docs/dev.bat` serves it with Windows' Python);
  otherwise on Windows run under WSL. Strict check: `venv-linux/bin/mkdocs build --strict`.
- **Venvs are named per OS** (ADR-0029): `venv-linux/` (makefiles, WSL, CI), `venv-windows/` (`.bat` scripts), and
  `venv-linux-test/` / `venv-windows-test/`. `clean` (make or `.bat`) keeps venvs; `distclean` (make or `.bat`) removes every venv, both OSes'. The
  coprocessor's venv in the extension is created on first start, never by the build (ADR-0030).
- Branching, versioning, releases: `CONTRIBUTING.md`.

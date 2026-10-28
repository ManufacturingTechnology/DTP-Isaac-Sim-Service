---
title: 'Dev environment and commands'
audience: agent
---

# Dev environment and commands

Portable setup and commands for working on this repo, for every collaborator. All paths are
**repo-relative** or built from documented variables; machine-specific paths belong in each person's
local settings or Claude memory, not here. Verify against the makefiles and scripts; may lag.

## Isaac Sim

- **`extension/isaac-sim`** is a (gitignored) symlink to the local Isaac Sim installation. It is the
  portable anchor for everything below: create it once per checkout.
  - Windows: `mklink /D extension\isaac-sim <install dir>` (needs an administrator prompt or Developer Mode; without
    the privilege, Windows error 1314. `mklink /J` makes a junction, which needs no privilege and works the same here.)
  - Linux: `ln -s <install dir> extension/isaac-sim`
- **Isaac Sim loads the extension from its own search path, not from the checkout you're in.** The folder is in the
  Extension Manager's settings (saved as `exts/userFolders` in Isaac Sim's per-user `user.config.json`); point it at
  this checkout's `extension/exts`. Seen 2026-10-28: after the repository moved, Isaac Sim kept starting the old
  checkout's copy until the folder was changed (the Kit log's `registered (path: …)` line shows which copy loaded).
- **Disabling and re-enabling the extension doesn't reload its submodules.** Kit re-imports only
  `amt.dtp.ise.extension`; `service/`, `ui/` and the rest stay as first imported (seen 2026-10-28: the log shows
  `reloading python module: amt.dtp.ise.extension` only, and the edited module's `.pyc` wasn't rewritten). After
  editing anything else, restart Isaac Sim.
- **The autoload entry names the version** (`exts/enabled` in `user.config.json`, e.g. `amt.dtp.ise-0.1.0-alpha.2`).
  After a version bump, check that the extension still loads; if not, enable it once in the Extension Manager to
  rewrite the entry ❓ (whether Kit treats the entry as an exact version wasn't checked).
- **Isaac Sim's Python:** `extension/isaac-sim/python.bat` (Windows) or `extension/isaac-sim/python.sh`
  (Linux) sets up the environment. The raw interpreter is `extension/isaac-sim/kit/python/python.exe`,
  which needs `PYTHONPATH` for bundled packages.
- **Bundled packages,** for offline scripts and probes with the raw interpreter (versions in the folder
  names vary by Isaac Sim version, so use globs):

  | Package | Location |
  |---|---|
  | USD (`pxr`) | `extension/isaac-sim/extscache/omni.usd.libs-*`; add it to `sys.path` and, on Windows, its `bin` folder to `os.add_dll_directory` |
  | fastapi, pydantic, numpy, uvicorn | `extension/isaac-sim/extscache/omni.kit.pip_archive-*/pip_prebundle` |
  | requests | `extension/isaac-sim/exts/omni.pip.cloud/pip_prebundle` |
  | cuMotion | `extension/isaac-sim/exts/isaacsim.robot_motion.cumotion/pip_prebundle` (also `os.add_dll_directory` it on Windows) |
  | PyYAML (for the cuMotion solver) | `extension/isaac-sim/exts/omni.pip.compute/pip_prebundle` |
  | NVIDIA Warp | `extension/isaac-sim/extscache/omni.warp.core-*` |

- **Isaac Sim 6.0.1 bundles** (verified by import): fastapi 0.120.4, pydantic 2.11.10, uvicorn 0.29.0,
  Python 3.12, cuMotion 1.1.0a2, Warp 1.13.0.
- **Isaac assets** (6.0) are in a public S3 bucket:
  `https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.0/Isaac/`
  (listable with `?list-type=2&prefix=`).

## Development layout

- `make dev` / `dev.bat` in `extension/` link `lib/src/amt/dtp/ise/lib` into the extension
  (`exts/amt.dtp.ise/amt/dtp/ise/lib`, a symlink in dev; a copy in builds). Edits to the lib are live
  in the extension.
- `lib/` is the pure-Python library (`amt-dtp-ise-lib`): control models, the APIs, clients, codegen.
  It has no Isaac Sim dependency, so it is testable anywhere.
- **Venvs are named per OS** (ADR-0029): `venv-linux/` (makefiles, WSL, CI) and `venv-windows/` (`.bat` scripts);
  test venvs `venv-linux-test/` and `venv-windows-test/` (ignored by the root `.gitignore`). Both platforms can be set
  up in one checkout without touching each other's venvs. The subproject `.gitignore` files and the makefiles'
  `distclean` (make or `.bat`) still cover the pre-ADR names (`venv/`, `venv-test/`) for older clones.
- **The coprocessor in the extension** (`exts/amt.dtp.ise/coprocessor/`, launched by `coprocessor/app.py`):
  - **Dev mode** (a link to the repo's `coprocessor/`, detected by `src/amt/dtp/iscp/coprocessor.py` being there):
    runs `python -m src.amt.dtp.iscp.coprocessor` from the per-OS venv that `coprocessor/dev.bat` or
    `make -C coprocessor dev` set up; a missing venv raises an error naming the script to run.
  - **Build mode** (the wheel plus `install.bat` / `install.sh` / `run.*`, copied from `coprocessor/install/`): runs the
    `amt-dtp-iscp` console script (`venv-windows/Scripts/amt-dtp-iscp.exe` or `venv-linux/bin/amt-dtp-iscp`). If the
    venv's Python or the console script is missing, `app.py` runs the installer first (it reuses an existing venv, so it
    also repairs a partial install).
  - **The venv is created on first start, never by the build** (ADR-0030): the zip holds no `venv-*`. The install takes
    ~2 minutes, needs Python and network access, and runs synchronously during the coprocessor service's start-up
    (`CoProcessorService._service_start`), so Isaac Sim is unresponsive meanwhile; so does `start()`'s 5 s wait.
    Known and accepted for now; moving both off the main loop is a follow-up.
  - **Experimental, not for releases:** `extension/build-prebuilt-venv.bat` (the build, then `install.bat` into the
    build folder, then `make package-prebuilt-venv`, whose zip steps are commented out) keeps the build-time tooling for
    later research. Its header lists what's missing.
  - VS Code (`coprocessor/.vscode/`): `launch.json` picks the interpreter per OS (`windows` / `linux` blocks, VS Code's
    platform-specific properties, V 2026-10-28); `settings.json` names `venv-linux` only (it takes one path), so pick
    `venv-windows` once on Windows.

## Tests

- **Lib suite:**
  - `lib/test.bat` (Windows, venv `venv-windows-test`) or `make -C lib test` (Linux, `venv-linux-test`) builds the
    wheel and runs `pytest tests` against it with the `[server]` extra.
  - Quick loop: a venv with `pytest fastapi uvicorn requests`, then `PYTHONPATH=lib/src pytest lib/tests`.
  - Also run it with Isaac's interpreter and bundled packages, to catch version differences (recipe under
    "Package validation" › Tests below). Export `PYTHONPATH` rather than only editing `sys.path`:
    `test_imports.py` imports the lib in a subprocess.
- **Extension and demo harnesses** (`docs/src/Agents/Scratch/harnesses/`, see its README for arguments):
  - `extension_harness.py` exercises AutomationComponent, transducers, MTConnect resolution, and the
    segment API (halt, configuration) against a real in-memory USD stage, with
    `omni`/`isaacsim`/PhysX stubbed. Run it with Isaac's raw interpreter and `PYTHONPATH` = `lib/src`,
    `pip_prebundle`, and `omni.pip.cloud/pip_prebundle`; arguments: the repo root and
    `extension/isaac-sim/extscache/omni.usd.libs-*`.
    An optional third argument, a folder with `AMT MR-A1.usda/.urdf/.xrdf` (the alpha testbeds
    repo's `Assets/AMT MR-A1`), adds two checks with **real cuMotion**: the refactored
    `ManipulatorRobotCuMotionActuator` and a `RobotApi` on a real `AutomationComponent`.
  - `kinematics_benchmark.py <repo> <asset dir>` checks and times the `CuMotionKinematicsSolver` offline
    (FK/IK, continuity, runtime frames, compiles; per-pose and batch timings). Run with Isaac's raw
    interpreter and `-I`; it sets its own paths.
  - `kinematics_service_probe.py <repo> <asset dir>` drives the kinematics service (ADR-0010) over HTTP with real
    cuMotion (MR-A1, plus Isaac Sim's bundled UR10 for self-collision) and smoke-tests the extension's
    `KinematicsService` with the configuration stubbed; `cumotion_thread_probe.py <repo> <urdf>` is the
    thread-safety probe. Both with Isaac's raw interpreter and `-I`; they set their own paths. The harnesses that
    call `os.add_dll_directory` need **absolute** paths for `<repo>` and `<usd_libs>` (WinError 87 otherwise).
  - `upper_layer_harness.py <repo> <usd_libs> <mr_a1 asset dir>` checks the planner and end-effector stack offline
    (the cuMotion planner, `stage_obstacles` on an in-memory stage, blending/CIRC on a real robot, end-effector combinations with
    USD-Site validation, the ISE `ApiController` on `ApiHost`). Isaac's raw interpreter and `-I`; absolute paths.
  - `demo_harness.py` runs the alpha-testbed demo assets with their real USD files and runs their client
    examples (arguments: also the testbeds repo root, or a scratch copy of it, and a client Python). The
    MR-A1 demo uses real cuMotion (the harness adds its paths).
  - Graduating them into the test suite is a follow-up.
- **The root `make test`** (the REQUIRED `test` job in `ci.yml`) runs `make -C lib test` and `make -C coprocessor
  test`: each builds its wheel and runs its `tests/` against the installed wheel in a throwaway venv. The
  co-processor suite (`coprocessor/tests/`) starts with a smoke test (the app builds; its version route answers).
  The test venvs (`venv-linux-test/`) are kept between runs: each run updates their dependencies and force-reinstalls the
  freshly built wheel (same version number, new contents). CI starts from a fresh checkout every time.

## Package validation (testbeds and assets)

Design and rule catalog in `Specs/package-validation.md`.

- **Module:** `lib/src/amt/dtp/ise/lib/validation.py`. `check_package_paths(root, use_pxr=True)`
  returns `PathIssue`s (pydantic: `rule`, `severity`, `file` relative to the root, `line`, `path`,
  `context`, `message`, `fix`); `RULES` is the catalog. Pure Python; `pxr` is optional and only used
  to export binary `.usd`/`.usdc` layers to text (otherwise `LAYER-NOT-CHECKED`).
- **Checks:** USD asset paths (findings in `delete` list ops are only info, `PATH-DELETE-LIST-OP`),
  behavior `script_path` in customData (`amt:dtp:behavior:data`; other keys are reported as
  `SCRIPT-PATH-LEGACY-KEY`), URDF/XML `filename=` and `file:`/`package://` values, absolute user paths
  in strings and JSON/YAML/XRDF, and USD files sharing a name stem in one folder (`USD-STEM-DUPLICATE`).
- **Enforced** (ADR-0020): `environment.validate_directory` returns the entry check plus one
  line per error-severity issue (`format_issue(..., with_fix=True)`); `archive()` / `extract()` raise
  `PackageValidationError` (`.errors`); the upload routes answer HTTP 422 with
  `{"message", "errors"}`; the client's `environment_upload` / `asset_upload` raise `HTTPError` listing
  them. Entry layer: `main` + `ENTRY_EXTS` (`.usd`, `.usda`, `.usdc`, `.usdz`), still found with
  `rglob`. Tests: `lib/tests/test_environment.py`.
- **CLI:** `python -m amt.dtp.ise.lib.validation [--json] [--no-pxr] [--min-severity LEVEL]
  [--list-rules] DIR…`; exit 1 on any error-severity issue.
  - With `pxr` from Isaac Sim (Isaac's `python.bat` does **not** provide `pxr`): run the raw interpreter
    with `PYTHONPATH="lib/src;<omni.kit.pip_archive-*>/pip_prebundle"` and pass
    `--usd-libs extension/isaac-sim/extscache/omni.usd.libs-*` (adds it to `sys.path` and its `bin` to
    the DLL path).
- **Tests:** `lib/tests/test_validation.py`, `lib/tests/test_environment.py` (synthetic fixtures; the
  `pxr` test skips without `pxr`). To run them with `pxr`: Isaac's raw interpreter with `PYTHONPATH`
  = `lib/src`, `pip_prebundle`, `omni.pip.cloud/pip_prebundle`, and a venv's `site-packages` for
  `pytest`; add `omni.usd.libs-*` to `sys.path` and its `bin` to the DLL path before `pytest.main`.

- **Cleanup (separate from validation, which never changes files):** `lib/src/amt/dtp/ise/lib/package_cleanup.py`,
  `python -m amt.dtp.ise.lib.package_cleanup <dir> [--apply]` (dry run by default). It fixes backslashed
  behavior `script_path` values in `.usda` files; tests in `lib/tests/test_package_cleanup.py`. Design:
  `Specs/package-validation.md`, "Validation is read-only; cleanup is separate".

## License check

`make` (repo root) ends with `make license-check`: `.github/scripts/check-licenses.py` installs what we ship or
install by default (`lib/dist/*.whl[server]`, `coprocessor/dist/*.whl`, the extension's
`config/requirements.txt`) into a throwaway venv and reads each package's declared license. Non-permissive or
unknown licenses fail the build unless the package is in the script's `REVIEWED` list with a reason; packages in
`FORBIDDEN` (e.g. `cascadio`) fail regardless. No extra tool is needed (it reads standard package metadata).
Each subproject's `THIRD-PARTY-NOTICES.md` lists what its artifact includes and what it only references. ADR-0025.
Failures print to the console (no report file); the nonzero exit fails `make` and therefore CI.

**Every build rebuilds** (the user, 2026-10-27): the root targets (`docs-zip`, `extension-zip`, `lib-wheel`) are phony,
the docs site is rebuilt from scratch each time, and the extension build starts from `clean`. The one reuse: the
extension build reuses an existing coprocessor wheel (`make -C coprocessor clean` forces a rebuild). Before this, an
artifact was rebuilt only when missing, and a stale docs site shipped inside both zips (seen 2026-10-27). The root
build runs docs, then the extension (`DOCS_BUILT=1`, so it doesn't rebuild the site again), then the lib wheel (the
extension build cleans `lib/`).

**Venvs are kept and updated, not rebuilt** (the user, 2026-10-27; per-OS names and the Windows scripts, ADR-0029,
2026-10-28): in `lib/`, `coprocessor/` and `docs/`, `clean` (`make clean` or `clean.bat`) removes build outputs only.
Each `venv-linux/` reinstalls only when its dependency file changes (`venv-linux/touchfile` depends on `pyproject.toml`
or `requirements.txt`); on Windows, `coprocessor/dev.bat` and `docs/dev.bat` reinstall when that file differs from the
copy kept inside `venv-windows/`, and `lib/test.bat` updates its test venv on every run, like `make test`. `make
distclean` (root or per subproject) and `distclean.bat` (in `lib/`, `coprocessor/`, `docs/`) also remove every venv,
both OSes' build and test venvs and the pre-ADR names, so either one leaves a fresh checkout (ADR-0029); the root
`make distclean` and `coprocessor/distclean.bat` also remove the co-processor wheel. The `.bat` scripts find Python like the coprocessor's `install.bat` (`python`, `py`, `python3`), and pause
only when run without an argument (`no_pause` when called from another script).

## Content check

`make` also runs `make content-check`: `.github/scripts/check-content.py` scans every file git would commit for
confidentiality and export-control markings, NVIDIA copyright notices, `.mdl` files, and the terms of a local
denylist, and fails on a match. ADR-0026.

- **Denylist:** `.claude/confidential-terms.txt` (gitignored) and `~/.claude/confidential-terms.txt`; one term per
  line, `#` comments, plain lines match case-insensitively, `re:` lines are regexes. CI has neither file.
- **Agent hook:** the same script with `--hook` runs before every Write/Edit/MultiEdit/NotebookEdit
  (`.claude/settings.json`) and blocks (exit 2, reason on stderr) a write into a tracked file of this or a sibling
  repository. It needs `python3` on `PATH` (on Windows, the Microsoft Store alias works).
- **Pre-commit (opt-in per clone):** `git config core.hooksPath .githooks` runs `check-content.py --staged`.
- **Exceptions:** a reviewed false positive goes into the script's `ALLOW` (path -> reason).
- **Not scanned:** binary files; text written by shell commands is caught by `make`/pre-commit, not the hook.

## Docs

- The docs venv is created by `docs/makefile` (`venv-linux/bin/…`). On Windows, run the makefile under
  WSL (`wsl -e bash -c "cd <repo>/docs && make"`), or use `docs/dev.bat` to serve.
  - `make`: public build (excludes `Agents/`).
  - `make dev`: serves with the agent knowledge base (`mkdocs.dev.yml`) on `127.0.0.1:4000` (WSL forwards it to
    Windows). On Windows, `docs/dev.bat` does the same with Windows' own Python, no WSL: its own venv
    `docs/venv-windows/`, requirements reinstalled when `requirements.txt` differs from the copy kept in the venv, the
    version read from `extension.toml` with the same one-line rule as `read-version.sh` (minus the semver check);
    `docs/clean.bat` / `distclean.bat` match the makefile's targets. Checked 2026-10-28 (Python 3.13, port changed in a temporary copy): the
    build, agent-docs nav, Mermaid and sidebars all work. **Not `0.0.0.0`:** `mkdocs serve` writes the serve address into the Mermaid script URL, the browser
    refuses `http://0.0.0.0:…` (`ERR_ADDRESS_INVALID`), and Material throws "Invalid script" on every page with a
    diagram, which stops its JavaScript (sidebar heights, TOC tracking). Seen 2026-10-28 in the browser pane on such a page.
    Page URLs end in `.html` (a directory-style URL is a 404 page with an empty TOC).
  - `extra.css` sets no height on `.md-sidebar__scrollwrap`. The old `height: 100%` rule collapsed both sidebars to 0
    whenever Material's script failed (Material sets that height inline from JS at ≥ 76.25em; the sidebar itself is
    `height: 0` there); without it the sidebars fall back to their content height. Measured 2026-10-28 with the script
    failing: 583 px / 951 px without the rule, 0 / 0 with it.
  - **Self-hosted assets:** Material's built-in `privacy` plugin (`mkdocs.yml`) downloads the fonts, Mermaid and the
    search-worker shim at build time into `assets/external/` (cache in `docs/.cache/`, gitignored), so a page view
    contacts only the site. The build therefore needs network access (CI has it). Mermaid's URL is patched inside the
    shared theme bundle as an absolute `site_url` path, so a local build opened from another origin (or the copy inside
    the extension zip) fetches Mermaid from the public site; everything else resolves locally. Checked 2026-10-27 in a
    browser against a scratch build. The shipped assets are listed in `docs/THIRD-PARTY-NOTICES.md`.
- **Strict check** (broken links, including user docs → `Agents/` links): `venv-linux/bin/mkdocs build --strict`
  (add `-f mkdocs.dev.yml` to include `Agents/`).
- Mermaid renders client-side, so mkdocs doesn't validate it. To check a diagram, parse it with
  mermaid.js (e.g. `mermaid.parse` in a browser).

## Shell quirks (Windows)

- `python` / `python3` may be Microsoft Store stubs that hang; use a venv or Isaac Sim's Python.
- Git Bash rewrites arguments that look like `/paths`; prefix with `MSYS_NO_PATHCONV=1`.
- `.bat` scripts call their siblings by full path (`call "%~dp0clean.bat"`): with `NoDefaultCurrentDirectoryInExePath`
  set (it is in Claude Code's shell, seen 2026-10-28), `cmd` doesn't run a bare `clean.bat` from the current folder
  ("not recognized"), so `call clean.bat || (...)` takes the error branch.
- **`RD /S /Q` failing leaves `ERRORLEVEL` at 0** (e.g. a folder still open in another process), so `RD x || (...)`
  never takes the error branch; and an `exit /b 1` in the same parenthesized block as a failed `RD` makes `cmd /c`
  return 0 (probed 2026-10-28; the same exit from a separate block returns 1). The clean/distclean scripts therefore
  check `IF EXIST` after each `RD`, set a flag, and exit from a block of their own.
- A folder can't be deleted while any process holds a handle on it. Seen 2026-10-28: VS Code's file search
  (`rg.exe --files --no-ignore --follow`, started by a VS Code node service, so `.gitignore` doesn't keep it out of
  venvs) held three folders inside `docs/venv-windows` for hours. Close or reload VS Code, or end those `rg.exe`
  processes, then rerun the distclean.
- In WSL, `ln -s target link` onto an existing link to a directory creates a new link *inside* the target (here: a
  `lib` link inside the lib source). `make -C coprocessor dev` therefore links only when the link is missing (seen
  2026-10-28 after Windows `dev.bat` had made the junction).
- In Git Bash, don't put backticks inside double-quoted `-c "…"` strings (the shell runs them as
  commands); prefer heredocs with a quoted delimiter, or script files.

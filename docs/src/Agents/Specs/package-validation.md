---
title: 'Package validation'
audience: both
---

# Package validation

> **Abstract.** A validation framework for testbed and asset packages: a catalog of rules with ids and
> severities, the structured issue format, where validation runs (CLI, the upload routes and the client;
> later CI, the packaging tool, an in-Isaac menu), how USD specifics are handled (anchored vs search paths,
> `delete` list ops, binary layers, behavior script paths in customData), and the separate cleanup step.
> Reflects the implementation: the path rules and the USD stem rule are implemented in
> `amt.dtp.ise.lib.validation` and enforced by `validate_directory` (so by `archive`, `extract`, the upload
> routes, and the client); validation is read-only, and cleanup (`amt.dtp.ise.lib.package_cleanup`) is a
> separate step. Archive rules and a structured entry check are planned. Verify against the code; may lag.

**Status:** partially implemented; this page describes what the code does, and marks the rest
**Planned**.
- Implemented: the rules in [Implemented](#implemented) and the CLI (`lib/src/amt/dtp/ise/lib/validation.py`,
  tests in `lib/tests/test_validation.py`); enforcement through `validate_directory` (ADR-0020, accepted;
  tests in `lib/tests/test_environment.py`); the cleanup module (`lib/src/amt/dtp/ise/lib/package_cleanup.py`,
  tests in `lib/tests/test_package_cleanup.py`).
- Decided by the user: [Decisions](#decisions) and
  [read-only validation](#validation-is-read-only-cleanup-is-separate).
- Planned (not built): `ENTRY-*` as rule ids, `ARCHIVE-*`, the other rules in [Planned](#planned), CI, the
  packaging tool, a composition-faithful mode, the report wrapper and suppression.
- [Open questions](#open-questions-for-the-user): parked by the user; not decided.

**Backs (user docs):** Getting Started › Environments ("Rules": references must be relative), and the
future packaging chapter for assets and testbeds (ADR-0018's bundling tooling).

Markers: **V** = verified (fetched source or a probe that was run; named), **❓** = unverified.

## Terms

- **Package:** a directory tree that is moved as a unit: a **testbed** (called *environment* in the
  code and the Getting Started docs) or an **asset** (a machine, robot, or end effector, with its USD,
  scripts, and descriptions; see `end-effector-package.md` for one asset kind).
- **Package root:** the top directory of the package. Everything the package needs must be inside it.
- **Entry layer:** the USD file Isaac Sim opens for a testbed: `main` with any USD extension
  (`.usd`, `.usda`, `.usdc`, `.usdz`; decision 2 below). See the open question on `default.*`.
- **Stored path:** any path written into a package file: USD asset paths, behavior `script_path`
  values, URDF mesh filenames, paths in YAML/JSON.

## Why

1. **Portability.** A package is zipped, uploaded, extracted into a temporary directory, and opened on
   another machine, often a Linux container. Absolute paths point nowhere there; backslashes are
   ordinary filename characters on POSIX, so `..\_global\x.py` is one file name, not a path
   ([POSIX pathname definition](https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap03.html#tag_03_254):
   filenames are separated by `<slash>` characters only, V fetched); a path leaving the root
   depends on whatever happens to sit next to the package.
2. **Privacy.** Absolute paths embed user names and home-folder layouts. The repo rule (AGENTS.md,
   "Privacy and portability") forbids them in tracked files; packages are shared further than the repo.
3. **Future tooling.** ADR-0018 plans per-testbed repositories, shared asset repositories, and
   build/bundle tooling. Validation is the gate that tooling runs; it needs stable rule ids and a
   machine-readable result now, so CI and the tools can grow around it.

## USD background

- **Asset paths** are delimited by `@...@` in `.usda` text, or `@@@...@@@` when the path contains `@`
  ([USD glossary: Asset](https://openusd.org/release/glossary.html#usdglossary-asset), V fetched).
- **Anchored vs search paths** (ArDefaultResolver, the fallback resolver): a path starting with `./` or
  `../` is *file-relative* and is always anchored to the layer that authors it. Any other relative path
  (`robot.usda`, `sub/x.usda`, `OmniPBR.mdl`) is a *search path*: it is anchored to the layer only if a
  file exists there ("look-here-first"); otherwise it stays unanchored and is resolved against the
  current working directory and the configured search paths
  ([`defaultResolver.cpp`: `_IsFileRelative`, `_IsSearchPath`, `_CreateIdentifier`](https://github.com/PixarAnimationStudios/OpenUSD/blob/release/pxr/usd/ar/defaultResolver.cpp),
  V fetched; [ArDefaultResolver docs](https://openusd.org/release/api/class_ar_default_resolver.html):
  "The first directory will always be the current working directory", `PXR_AR_DEFAULT_SEARCH_PATH`,
  V fetched).
  - **Probe** (USD 0.25.11 from Isaac Sim 6.0.1's `omni.usd.libs`, offline, a scratch script): with a layer at `sub/main.usda`, `Ar.GetResolver().CreateIdentifier(p, anchor)`
    anchored `./x.usda` and `x.usda` (both existed next to the layer) but left `sub/x.usda` and
    `missing.usd` unanchored (no file at the anchored location). The registered resolver was
    `ArDefaultResolver` (V).
  - Inside Isaac Sim the primary resolver is Omniverse's `OmniUsdResolver`, which may differ in detail
    ❓ (not checked).
  - **Consequence:** prefer `./`-prefixed paths in packages; a bare path that is missing next to its
    layer silently falls back to search paths. Material built-ins such as `@OmniPBR.mdl@` and
    `@OmniGlass.mdl@` rely on this (MDL search paths in Kit ❓); they are reported as `PATH-SEARCH` (info),
    not as errors, and in a probe over a set of testbeds they were almost all of its findings.
- **List ops.** Composition fields (`payload`, `references`, `inherits`, …) are list-edited
  with `prepend`, `append`, `delete`, or an explicit list
  ([USD glossary: List Editing](https://openusd.org/release/glossary.html#usdglossary-listediting), V fetched). A
  `delete` entry removes a value from the composed result, **but its asset path stays stored in the
  layer.**
  - **Probe** (same setup): `Sdf.PrimSpec.payloadList.deletedItems` returns the deleted
    `file:`/absolute entries; `Sdf.Layer.GetCompositionAssetDependencies()` and
    `UsdUtils.ExtractExternalReferences()` do **not** return them (they report applied arcs only);
    `UsdUtils.ModifyAssetPaths()` does visit them (V). So dependency APIs alone would miss the leaks;
    the validator scans the layer text instead.
  - API descriptions: [`SdfLayer`](https://openusd.org/release/api/class_sdf_layer.html)
    (`GetCompositionAssetDependencies`: "paths of all assets this layer depends on due to composition
    fields", V fetched), [`UsdUtils` dependencies](https://openusd.org/release/api/dependencies_8h.html)
    (`ExtractExternalReferences` "No recursive chasing of dependencies", `ComputeAllDependencies`
    reports `unresolvedPaths`, `ModifyAssetPaths` callbacks receive the authored value; V fetched).
- **`.usd` can be text or binary.** The `.usd` format is backed by `usda` or `usdc`; new files default to
  `usdc` ([`sdf/usdFileFormat.h`](https://github.com/PixarAnimationStudios/OpenUSD/blob/release/pxr/usd/sdf/usdFileFormat.h),
  V fetched). The validator sniffs content (`#usda` vs the `PXR-USDC` magic), not the extension.
  **Probe:** every testbed `.usd` file sampled started with `PXR-USDC` (binary) (V).
- **Binary layers** need `pxr`. With `pxr`, the validator opens the layer with
  `Sdf.Layer.OpenAsAnonymous` and scans `ExportToString()`, which preserves `delete` list ops and
  customData (probe V). Line numbers are then unavailable. Without `pxr`, each binary layer gets
  `LAYER-NOT-CHECKED`. Exporting a 50 MB crate layer took about 0.4 s (probe V).

## Behavior script paths (as built in the extension)

From `extension/exts/amt.dtp.ise/amt/dtp/ise/entity/behavior.py` (`BehaviorModel`,
`BehaviorScriptLoader.get_absolute_script_path`, `get_prim_source_file`,
`get_external_reference_prim_source_file`), V read:

- Behavior data is JSON in prim customData at key path **`amt:dtp:behavior:data`** (nested
  `dictionary amt { dictionary dtp { dictionary behavior { string data = '{"script_path": ...}' } } }`).
  Some files also carry **`amt:behavior:script`**, a key the extension doesn't read
  (`SCRIPT-PATH-LEGACY-KEY`).
- Resolution order: (1) `os.path.isfile(script_path)`, i.e. **relative to the process's current working
  directory** or absolute; (2) relative to the folder of the prim's *source file*: the asset path of the
  last payload in the prim stack (`Sdf.ComputeAssetPathRelativeToLayer`), walking up to ancestors, or
  the stage's root layer.
- Step (1) means a relative script path can resolve to an unrelated file in Isaac Sim's working
  directory. **Kept as is** by user decision ([decision 4](#decisions)); `SCRIPT-PATH-CWD`
  (planned) would only report it.

**Static approximation (implemented):** without composing a stage, the validator treats a script path
as resolved if it exists relative to the authoring layer **or to any layer that layer composes**
(non-deleted sublayers, references, payloads, transitively). This covers the common pattern of an
`over` in a testbed's `main.usda` on a prim that comes from a payloaded asset (the runtime base is
then the asset's folder); checking only the authoring layer's folder reports such overrides as false
`SCRIPT-PATH-UNRESOLVED` findings. **Planned (not built):** a composition-faithful mode that opens the
entry layer as a `Usd.Stage` and reuses the loader's own resolution, for the in-Isaac menu and CI with `pxr`.

## Rule catalog

The implemented catalog is `RULES` in `validation.py` (id → severity, one-line summary; printed by
`--list-rules`). Severities are fixed per rule.

Severity semantics:
- **error:** the package is not portable, is ambiguous, or leaks a user path in data that is used;
  `validate_directory` (so `archive`, `extract`, the upload routes, the client) refuses it, and so will
  the planned packaging tool.
- **warning:** probably broken or not self-contained; reported, not blocking.
- **info:** worth knowing (search-path lookups, unchecked files, legacy data, stale `delete` entries).

### Implemented

| Rule | Sev. | Applies to | Checks | Why |
|---|---|---|---|---|
| `PATH-FILE-URL` | error | USD asset paths; URDF/XML `filename=` attributes and any attribute value starting with `file:` or `package://` | value starts with `file:` | absolute and machine-specific; leaks user names |
| `PATH-ABSOLUTE` | error | same | drive letter (`D:`), leading `/` or `\` (POSIX, UNC), `~` | not portable; leaks |
| `PATH-REMOTE` | warning | same | `scheme://` (omniverse, http(s), s3, …) | target is not in the package; needs network and credentials |
| `PATH-PACKAGE-URL` | warning | same (in practice URDF/XML) | `package://` | needs a ROS package mapping the importer may not have ❓ |
| `PATH-BACKSLASH` | error | relative stored paths | contains `\` | separators are `/` on Linux |
| `PATH-ESCAPES-ROOT` | error | relative stored paths | normalized target is outside the root | depends on files next to the package |
| `PATH-UNRESOLVED` | warning | relative stored paths (not `delete` entries) | target missing (after `\`→`/`); skipped for templates (`<UDIM>`, `#`, expressions) | broken reference |
| `PATH-SEARCH` | info | bare USD paths | not found next to the layer, so resolved via search paths | fine for MDL built-ins, otherwise not packaged |
| `PATH-DELETE-LIST-OP` | info | entries of `delete` list ops (`delete payload`, `delete references`, …) | any of the `PATH-*` form findings above (file: URL, absolute, remote, package URL, backslash, escapes the root); the message names the underlying rule | dropped at composition, so loading is unaffected; the path is still stored and may leak a local folder (decision 3) |
| `USD-STEM-DUPLICATE` | error | all USD files (`.usd`, `.usda`, `.usdc`, `.usdz`) | two or more in one folder share a file-name stem (case-insensitive), e.g. `main.usd` + `main.usda`; one issue per group, on the first file, `path` lists them all; for stem `main` the message says the entry layer is ambiguous | which file is meant is ambiguous (decision 2) |
| `SCRIPT-PATH-FILE-URL`, `-ABSOLUTE`, `-REMOTE`, `-BACKSLASH`, `-ESCAPES-ROOT` | error | `script_path` in customData JSON | as the `PATH-*` rules (`package://` counts as `REMOTE`); `REMOTE` is an error because the loader needs a local file | separate ids: resolved by the extension, not by Ar |
| `SCRIPT-PATH-UNRESOLVED` | warning | `script_path` under `amt:dtp:behavior:data` | not found from the authoring layer's folder or any composed layer's folder (no search-path fallback); skipped for templates | the behavior will fail to load |
| `SCRIPT-PATH-JSON` | warning | strings under `amt:dtp:behavior:data` mentioning `script_path` | no `script_path` value found, neither by parsing the string as JSON nor in a `"script_path": "…"` pair | malformed behavior data |
| `SCRIPT-PATH-LEGACY-KEY` | info | `script_path` under any other customData key (e.g. `amt:behavior:script`) | the extension does not read it; only form rules (absolute, backslash, …) apply | stale data; delete or migrate |
| `TEXT-ABSOLUTE-PATH` | warning | USD string literals (e.g. `doc`; not those whose `script_path` values were checked above), `.json`, `.yaml`, `.yml`, `.xrdf` | `file:` URLs, drive-letter paths, home folders (`/home` or `/Users` followed by a user name and `/`, WSL `/mnt/<drive>/Users`) | privacy; POSIX paths in general are not flagged because USD prim paths look like them |
| `LAYER-NOT-CHECKED` | info | `.usd`/`.usdc` without `pxr`, `.usdz`, unknown content | not checked | so a clean result is never silent about gaps |
| `LAYER-UNREADABLE` | warning | any scanned file | could not be opened or parsed | |

Notes:
- **`delete` list ops** are checked for form, and every finding is reported as `PATH-DELETE-LIST-OP`
  (info) instead of its usual rule; they are never checked for existence. The user decided this for
  absolute paths (decision 3); applying it to backslash and escapes-root entries too is this
  implementation's reading of the same reason (a deleted entry can't affect loading), so a `../`
  entry in a `delete` list does not fail an upload either. Revisit if that reading is wrong.
- **Stem comparison is case-insensitive** (`Robot.usd` + `robot.usda` is a duplicate): the entry lookup
  compares stems case-insensitively, and the two can't coexist on Windows file systems.
- **Layer `doc` strings** often name the file a layer was converted from, as an absolute path; such layers
  get `TEXT-ABSOLUTE-PATH` warnings (seen in binary `.usd` testbed layers, V probe).
- **Comments** (`#`, `//`, `/* */`) are skipped in `.usda`, so a commented-out path is not reported. XML
  comments are not skipped.
- **Directories skipped:** `.git`, `__pycache__`, `venv`, `.venv`, the repo's per-OS venvs (`venv-linux`,
  `venv-windows`, `venv-linux-test`, `venv-windows-test`; ADR-0029), `node_modules`. Directory symlinks
  are not followed.

### Planned

| Rule | Sev. | Checks |
|---|---|---|
| `ENTRY-MISSING` | error | testbed has no entry layer. **Enforced already** as a plain message in `validate_directory` (no rule id yet); a rule id comes with `check_package` |
| `ENTRY-AMBIGUOUS` | warning | entry candidates in different folders, or one only in a subfolder: `get_environment_usd_path` uses `rglob`, so a nested `assets/x/main.usd` counts and the first match (file-system order) wins (open question 2). Same-folder duplicates are covered by `USD-STEM-DUPLICATE` |
| `PATH-NOT-ANCHORED` | info | bare relative USD path that resolves next to its layer; suggest `./` so a later missing file can't fall back to search paths |
| `PATH-CASE-MISMATCH` | error | path matches a file only case-insensitively (works on Windows, fails on Linux) |
| `PATH-SYMLINK-OUTSIDE` | warning | a symlink inside the package points outside the root |
| `SCRIPT-PATH-CWD` | warning | relative `script_path` that would also match a file in the working directory (loader step 1) |
| `TEXT-ABSOLUTE-PATH` for `.py`, `.md`, `.bat`, `.sh`, `.toml` | warning | the same privacy patterns in scripts and notes |
| `ARCHIVE-ROOT-DIR` | error | the zip has a single top-level folder instead of the package contents (Getting Started › Environments) |
| `ARCHIVE-NAME` | warning | the zip name differs from the package id |
| `ARCHIVE-UNSAFE-MEMBER` | error | member names that are absolute or contain `..`. Python's `ZipFile.extract` strips these ([zipfile docs](https://docs.python.org/3/library/zipfile.html#zipfile.ZipFile.extract), V fetched), but the docs only say the module "attempts to prevent" escapes, so reject them up front |
| `EE-*` | per spec | end-effector package checks from `end-effector-package.md` § Validation (merged XRDF loads, FK matches Sites, frame names) |
| `CONFIG-INVALID` | error | `main.json` does not validate against the testbed configuration model |

## Result format

Each finding is a `PathIssue` (pydantic, frozen):

| Field | Meaning |
|---|---|
| `rule` | rule id from the catalog |
| `severity` | `error` / `warning` / `info` (fixed per rule) |
| `file` | POSIX path **relative to the package root** (never absolute, so results can be stored and shared) |
| `line` | 1-based line for text files; `null` for binary layers |
| `path` | the offending stored value, as authored |
| `context` | where it was found: the field (`delete payload`, `asset inputs:file`), the customData key path (`amt:dtp:behavior:data`), or the XML attribute (`filename=`) |
| `message` | human-readable explanation |
| `fix` | suggested fix, when there is an obvious one |

- The CLI's `--json` output is `{ "<root as given>": [PathIssue, …], … }`. Note that `path` may itself
  contain a user path (that is the finding), so redact before storing reports in a repo.
- **Planned:** a `ValidationReport` wrapper `{ root_name, package_kind, issues, files_checked,
  files_not_checked, tool_version }`, and a per-package suppression file (e.g. `package-validation.yaml`
  with rule ids and globs, plus a built-in allowlist for MDL built-ins such as `OmniPBR.mdl`).

## Where validation runs

| Where | When | Behavior |
|---|---|---|
| **CLI** (implemented) | `python -m amt.dtp.ise.lib.validation [--json] [--usd-libs DIR] [--no-pxr] [--min-severity LEVEL] [--list-rules] DIR…` | prints issues relative to each root; exit 1 if any error-severity issue, else 0 |
| **CI** (planned) | testbed and asset repositories (ADR-0018), on push | run the CLI with `pxr` available; fail on errors |
| **Packaging / bundling tool** (planned, ADR-0018) | before writing an archive or bundle | refuse on errors; include the report in the bundle |
| **`validate_directory` → `archive()` / `extract()` / upload routes / client** (implemented) | `archive()` before zipping (the client's `environment_upload`/`asset_upload` of a directory); `extract()` on the server (`POST /environment/upload`, `POST /asset/upload`) | fails on any error-severity issue (plus a missing entry layer); see [below](#relation-to-validate_directory) |
| **In-Isaac menu** (later) | on the open stage | composition-faithful mode (Usd.Stage); jump to the prim |

## Relation to `validate_directory`

`lib/src/amt/dtp/ise/lib/environment.py` (as built):
- `validate_directory(dir, *, use_pxr=True)`: `None` if valid, else a list of strings. It checks that the
  directory exists, then (1) that a file with stem `main` (compared case-insensitively) and a USD extension
  (`ENTRY_EXTS` in `validation.py`: `.usd`, `.usda`, `.usdc`, `.usdz`) exists **anywhere below** the
  directory (`get_environment_usd_path`, `rglob`), and (2) every error-severity issue of
  `check_package_paths`, one string each via `format_issue(issue, with_fix=True)`:
  `file[:line]: error RULE: message [path] (context); fix: …`, with `file` relative to the package root.
  Warnings and info never fail.
- `package_errors(dir, *, use_pxr=True)` returns just (2).
- `validate_archive(zip)` checks existence and the `.zip` extension; content validation is a `TODO`.
- `archive()` and `extract()` raise **`PackageValidationError`** (an `Exception` subclass) with `.errors`
  (the list) and a message with one ` - ` line per error. `extract()` deletes its temp directory when
  validation fails.
- `lib/src/amt/dtp/ise/lib/asset.py` is an unused twin (nothing calls it; the asset routes use
  `environment.extract`): it applies the same checks (entry layer plus `package_errors`) but raises a plain
  `Exception`.
- **Upload routes** (`extension/exts/amt.dtp.ise/amt/dtp/ise/api/routers.py`, `environment_upload` and
  `asset_upload`; both call `environment.extract`, so an asset needs a `main.*` entry too): an upload
  that isn't a `.zip` with a zip content type is 415; a `PackageValidationError` from `extract()` becomes **HTTP 422** with
  `detail = {"message": …, "errors": [...]}`; other extraction failures are 500. The existing
  environment/asset of the same name is removed only **after** the upload validated, and the temp folders
  are deleted on success and failure.
- **Client** (`apiclient.py`): `environment_upload` / `asset_upload` of a directory fail locally in
  `archive()` (`PackageValidationError`); for a 422 from the server they raise `requests.HTTPError` whose
  message lists the errors (`_raise_for_upload_status`).
- Validation in the extension uses `pxr` (binary layers are checked); a client without `pxr` reports
  binary layers as `LAYER-NOT-CHECKED` (info) and leaves them to the server. It runs synchronously inside
  the async upload handler, which runs on the ISE API's own server thread (an `ApiHost`, ADR-0024), not on
  Isaac Sim's main loop; it blocks that API's other requests meanwhile. A large testbed takes seconds (a
  probe with `pxr` over a set of about a dozen testbeds, binary layers included, took roughly 9 s per pass).
  Thread safety of `Sdf.Layer.OpenAsAnonymous` off Kit's main thread was not checked ❓.

**Planned (not built):** a `check_package(root, kind)` that runs `ENTRY-*` as rules plus
`check_package_paths`, returning one issue list.

## Decisions

Decided by the user:

1. **Fail upload on errors: yes.** `validate_directory`, and so `archive()`, `extract()`, the upload
   routes, and the client, fail when `check_package_paths` reports any error-severity issue. The error
   lists each issue (rule id, file relative to the package root, line, path, fix) so the uploader can fix
   it. Warnings and info don't fail.
2. **Entry layer: any USD flavor.** Found by the name rule (stem `main`), with `.usd`, `.usda`,
   `.usdc`, or `.usdz`. A `.usdz` package opens as a stage through its default layer, which
   must be the first file in the zip ([USDZ spec](https://openusd.org/release/spec_usdz.html): "the
   first file in the package must be a native usd file", V fetched; opening a `.usdz` entry
   through the extension's `open_stage_async` was not tested ❓). Rule **`USD-STEM-DUPLICATE`**
   (error, everywhere in the package): no two USD files in one folder may share a stem; it makes the
   entry ambiguous when it hits `main`.
3. **Absolute paths left in `delete` list ops: leave them be.** They must not fail validation;
   detection stays, as `PATH-DELETE-LIST-OP` (info), with a message that they don't affect loading but
   leak local paths. (Extended here to all form findings on `delete` entries; see the catalog notes.)
   Such entries are common: in a probe over a set of testbeds, every absolute composition path was a
   `file:` URL in a `delete payload` list op, and no live (non-deleted) arc was absolute (V).
4. **Script lookup: keep as is.** The extension's working-directory lookup (step 1 of the loader) is
   not changed.

## Validation is read-only; cleanup is separate

**Decided by the user:** validation must never modify files. Fixing common issues (such as
backslashes) is a **separate function**, which can be part of the asset/testbed build or packaging
process but is not part of validation. So the backslash rules stay **errors** in validation, and the
behavior-script loader does **not** normalize separators.

**As built:** `lib/src/amt/dtp/ise/lib/package_cleanup.py`
- `fix_script_path_backslashes(root, *, apply=False)` returns the fixes (`Fix(file, line, old, new,
  applied)`, `file` relative to the root); a dry run by default.
- It edits only `.usda` text (skipping the same directories as validation), and only the `script_path`
  JSON value inside single-quoted USD strings: USD-unescape, JSON-decode, replace `\` with `/`, re-encode.
  The rest of the file stays byte for byte, including its newline style. Binary layers are not touched.
- CLI: `python -m amt.dtp.ise.lib.package_cleanup <dir> [--apply]` (prints each fix; exit code 0).
- Tests: `lib/tests/test_package_cleanup.py` (dry run, apply, idempotence, newline style, and a check
  that validation leaves every file unchanged).

**Planned (not built):** more fixers in the same module (e.g. absolute → relative paths where the target is
inside the package; binary `.usd` script paths through `pxr`), and a `--fix` step in the future packaging tool
that runs cleanup, then validation.

## Open questions for the user

**Parked by the user; not decided.** The code does what the sections above describe until
they are decided.

1. **`default.*` entry naming:** the testbeds repo (`DTP-Isaac-Sim-Service-Alpha-Testbeds`) names its
   entry `default.usd(a)`, which `validate_directory` rejects (no `main.*`). Decision 2 kept `main` only; accept `default.*` too, or
   declare the entry in a manifest?
2. **Entry in a subfolder:** `get_environment_usd_path` uses `rglob`, so a `main.*` anywhere below the
   root counts and the first match wins (see `ENTRY-AMBIGUOUS`). Restrict it to the root?
3. **`delete` entries beyond absolute paths:** this implementation also downgrades backslash and
   escapes-root findings on `delete` entries to info (same reason as decision 3). Confirm.
4. **Legacy `amt:behavior:script` data:** keep reporting it as info, raise it to a warning, or provide a
   migration that deletes it?
5. **Suppression:** is a per-package suppression file wanted, or should every finding be fixed at the
   source?
6. **`PATH-SEARCH` for non-MDL paths:** should a bare path that is missing next to its layer and isn't a
   known built-in be a warning rather than info?

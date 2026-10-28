"""Package validation: check that a testbed or asset package is self-contained and portable.

A *package* is a directory tree (a testbed, a.k.a. "environment", or an asset) that should work
after it is zipped, moved to another machine, or extracted into a Linux container. This module
implements the path rules of the package-validation spec (``docs/src/Agents/Specs/package-validation.md``):
every path stored in the package must be relative, use forward slashes, stay inside the package
root, and (where it can be checked) point at a file that exists.

What is scanned:

- ``.usda`` files, and ``.usd`` files whose content is text: every asset path (``@...@`` and
  ``@@@...@@@``), including those inside list ops such as ``delete payload`` (a deleted entry is
  dropped at composition, so it is only reported as info, but its path is still stored in the
  file), and every string literal: behavior ``script_path`` values in customData JSON, plus
  absolute user paths.
- All USD files: no two in one folder may share a name stem (``main.usd`` + ``main.usda``).
- Binary ``.usd`` / ``.usdc`` layers: exported to text with ``pxr.Sdf`` when ``pxr`` is importable
  and scanned the same way (without line numbers); otherwise reported as ``LAYER-NOT-CHECKED``.
- ``.urdf`` / ``.xml``: ``filename=`` attributes and any ``file:`` / ``package://`` attribute value.
- ``.xrdf`` / ``.yaml`` / ``.yml`` / ``.json``: absolute user paths and ``file:`` URLs only.

``amt.dtp.ise.lib.environment.validate_directory`` (and so ``archive``, ``extract`` and the upload
routes) fails on any error-severity issue.

Pure Python, no Isaac Sim dependency; ``pxr`` is an optional import.

CLI::

    python -m amt.dtp.ise.lib.validation [--json] [--usd-libs DIR] [--min-severity LEVEL] DIR [DIR ...]

Exits 1 if any package has an error-severity issue, else 0.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field as dc_field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}


class PathIssue(BaseModel):
    """One finding. ``file`` is POSIX-style and relative to the package root; ``line`` is 1-based
    (``None`` for binary layers); ``path`` is the offending stored value; ``context`` is where it
    was found (e.g. ``delete payload``, ``amt:dtp:behavior:data``, ``filename=``)."""

    model_config = ConfigDict(frozen=True)

    rule: str
    severity: Severity
    file: str
    line: Optional[int] = None
    path: Optional[str] = None
    context: Optional[str] = None
    message: str
    fix: Optional[str] = None


# Rule catalog: id -> (severity, one-line summary). The spec is the long form.
RULES: Dict[str, Tuple[Severity, str]] = {
    "PATH-FILE-URL": (Severity.ERROR, "stored path is a file: URL (absolute, machine-specific)"),
    "PATH-ABSOLUTE": (Severity.ERROR, "stored path is absolute (drive letter, /, UNC or ~)"),
    "PATH-REMOTE": (Severity.WARNING, "stored path is a remote URL (omniverse://, http(s)://, ...); not packaged"),
    "PATH-PACKAGE-URL": (Severity.WARNING, "URDF package:// URI; depends on a ROS package mapping"),
    "PATH-BACKSLASH": (Severity.ERROR, "relative path uses backslashes; fails on Linux"),
    "PATH-ESCAPES-ROOT": (Severity.ERROR, "relative path leaves the package root"),
    "PATH-UNRESOLVED": (Severity.WARNING, "relative path target does not exist in the package"),
    "PATH-SEARCH": (Severity.INFO, "bare USD path not found next to the layer; resolved via search paths"),
    "PATH-DELETE-LIST-OP": (Severity.INFO, "non-portable path in a delete list op; no effect on loading, but stored"),
    "USD-STEM-DUPLICATE": (Severity.ERROR, "USD files in one folder share a name stem (e.g. main.usd + main.usda)"),
    "SCRIPT-PATH-FILE-URL": (Severity.ERROR, "behavior script_path is a file: URL"),
    "SCRIPT-PATH-ABSOLUTE": (Severity.ERROR, "behavior script_path is absolute"),
    "SCRIPT-PATH-REMOTE": (Severity.ERROR, "behavior script_path is a URL; the loader needs a local file"),
    "SCRIPT-PATH-BACKSLASH": (Severity.ERROR, "behavior script_path uses backslashes; fails on Linux"),
    "SCRIPT-PATH-ESCAPES-ROOT": (Severity.ERROR, "behavior script_path leaves the package root"),
    "SCRIPT-PATH-UNRESOLVED": (Severity.WARNING, "behavior script_path not found from any candidate base"),
    "SCRIPT-PATH-JSON": (Severity.WARNING, "behavior data mentions script_path but is not parseable JSON"),
    "SCRIPT-PATH-LEGACY-KEY": (Severity.INFO, "script_path under a customData key the extension doesn't read"),
    "TEXT-ABSOLUTE-PATH": (Severity.WARNING, "absolute user path or file: URL in a string/text value"),
    "LAYER-NOT-CHECKED": (Severity.INFO, "layer not checked (binary without pxr, or usdz/unknown format)"),
    "LAYER-UNREADABLE": (Severity.WARNING, "file could not be opened or parsed"),
}

# The customData key the extension reads behavior data from (BehaviorModel.custom_data_key in the
# extension's entity/behavior.py). Other keys holding script_path are legacy data.
BEHAVIOR_DATA_KEY = "amt:dtp:behavior:data"

# Venvs: the repo's per-OS names (ADR-0029) and the common ones.
SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "venv-linux", "venv-windows", "venv-linux-test",
             "venv-windows-test", "node_modules"}

USD_EXTS = {".usd", ".usda", ".usdc", ".usdz"}
# Testbed entry layer: a file named ``main`` with any USD extension (ADR-0020).
ENTRY_STEM = "main"
ENTRY_EXTS = (".usd", ".usda", ".usdc", ".usdz")
XML_EXTS = {".urdf", ".xml"}
TEXT_EXTS = {".xrdf", ".yaml", ".yml", ".json"}

# Fields whose asset paths are composition arcs (used to find the layers a layer composes).
ARC_FIELDS = {"subLayers", "payload", "references"}

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_URL_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]+)://")
# Unexpanded templates/expressions whose existence can't be checked statically.
_TEMPLATE_RE = re.compile(r"<UDIM>|<UVTILE>|#|`|\$\{")
# Absolute locations and file: URLs inside free text (strings, JSON, YAML): file: URLs, drive-letter
# paths, and the per-user home folders of Linux, macOS and WSL (the same families as the repo's
# privacy grep). POSIX paths in general are not matched: USD prim paths look like them.
_TEXT_ABS_RE = re.compile(
    r"file:/+[^\s\"'<>,]*"
    r"|(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)[^\s\"'<>,]*"
    r"|/(?:home|Users)/[^/\s\"'<>,]+/[^\s\"'<>,]*"
    r"|/mnt/[a-z]/[Uu]sers/[^\s\"'<>,]*"
)
_XML_ATTR_RE = re.compile(r"""([A-Za-z_][\w:.\-]*)\s*=\s*(["'])(.*?)\2""")
_FIELD_RE = re.compile(r"([A-Za-z_][\w:.\[\] ]*?)\s*$")


# --------------------------------------------------------------------------------------------
# Path classification
# --------------------------------------------------------------------------------------------

def _issue(rule: str, file: str, line: Optional[int], message: str, *, path: Optional[str] = None,
           context: Optional[str] = None, fix: Optional[str] = None) -> PathIssue:
    return PathIssue(rule=rule, severity=RULES[rule][0], file=file, line=line, path=path,
                     context=context, message=message, fix=fix)


def _is_within(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(child), os.path.normcase(parent)]) == os.path.normcase(parent)
    except ValueError:  # different drives on Windows
        return False


def _check_path(value: str, *, prefix: str, root: Path, bases: Sequence[Path], rel_file: str,
                line: Optional[int], context: Optional[str], resolve: bool = True,
                search_paths: bool = False) -> List[PathIssue]:
    """Classify one stored path. ``prefix`` is ``PATH`` or ``SCRIPT-PATH``.

    ``bases``: directories the path may be relative to; the first is the authoring file's own
    folder. It resolves if it exists inside the root relative to any of them, and escapes the root
    only if it escapes from all of them. ``resolve=False`` checks only the path's form (absolute,
    URL, backslash). ``search_paths``: USD semantics, where a bare relative path (not ``./`` or
    ``../``) that is not found next to the layer is looked up on the resolver's search paths.
    """
    v = value.strip()
    if not v:
        return []
    # Entries of a ``delete`` list op are dropped at composition, so their form can't break loading
    # (ADR-0020): each finding on them is reported as PATH-DELETE-LIST-OP (info).
    deleted = bool(context) and context.split()[0] == "delete"
    lower = v.lower()

    def issue(kind: str, message: str, fix: Optional[str]) -> List[PathIssue]:
        if deleted:
            return [_issue("PATH-DELETE-LIST-OP", rel_file, line,
                           f"{message} ({prefix}-{kind}), but only in a delete list op: dropped at composition, so "
                           "it does not affect loading; the path is still stored in the file and may leak a "
                           "local folder", path=v, context=context,
                           fix="Optional: remove the stale entry from the delete list")]
        return [_issue(f"{prefix}-{kind}", rel_file, line, message, path=v, context=context, fix=fix)]

    if lower.startswith("file:"):
        return issue("FILE-URL", "path is a file: URL",
                     "Use a path relative to this file (e.g. ./sub/file.usd), or remove the entry")
    if _DRIVE_RE.match(v) or v.startswith(("/", "\\", "~")):
        return issue("ABSOLUTE", "path is absolute",
                     "Use a path relative to this file (e.g. ./sub/file.usd), or remove the entry")
    m = _URL_RE.match(v)
    if m:
        if m.group(1).lower() == "package" and prefix == "PATH":
            return issue("PACKAGE-URL", "package:// URI depends on a ROS package mapping",
                         "Use a path relative to the URDF file (e.g. meshes/link1.stl)")
        return issue("REMOTE", f"path is a {m.group(1)}:// URL; the target is not in the package",
                     "Copy the asset into the package and reference it relatively")

    issues: List[PathIssue] = []
    if "\\" in v:
        issues += issue("BACKSLASH", "path uses backslashes", "Use forward slashes: " + v.replace("\\", "/"))
    if not resolve:
        return issues
    norm = v.replace("\\", "/")
    targets = [os.path.normpath(os.path.join(str(b), norm)) for b in bases]
    inside = [t for t in targets if _is_within(t, str(root))]
    if not inside:
        return issues + issue("ESCAPES-ROOT", "path leaves the package root",
                              "Move the target into the package, or the package root up")
    if deleted or _TEMPLATE_RE.search(norm) or any(os.path.exists(t) for t in inside):
        return issues
    if search_paths and not norm.startswith(("./", "../")):
        return issues + issue("SEARCH", "not found next to this layer; resolved through search paths "
                              "(fine for built-ins such as OmniPBR.mdl, otherwise not in the package)", None)
    where = "this file" if len(bases) == 1 else "this file or any layer it composes"
    return issues + issue("UNRESOLVED", f"target does not exist (relative to {where})",
                          "Fix the path or add the missing file")


# --------------------------------------------------------------------------------------------
# USDA text scanning
# --------------------------------------------------------------------------------------------

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}
_SPECIAL_RE = re.compile(r"[\n#/@\"'={}]")


def _unescape_usd_string(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), s, flags=re.DOTALL)


@dataclass
class _Token:
    kind: str                # "asset" or "string"
    value: str               # asset path as authored, or the unescaped string
    line: int
    field: Optional[str]     # statement before the last '=', e.g. "delete payload", "string data"
    key: Optional[str]       # for strings in dictionaries: the key path, e.g. "amt:dtp:behavior:data"


def _scan_usda(text: str) -> Iterator[_Token]:
    """Tokenize USDA text into asset paths and string literals (best effort, not a full parser).

    Tracks the field each token belongs to (the words before the last ``=``) and, for strings in
    nested ``dictionary`` blocks (customData), the ``:``-joined key path. Comments (``#``, ``//``,
    ``/* */``) are skipped."""
    i, n, line = 0, len(text), 1
    field: Optional[str] = None
    after_eq = False         # nothing but whitespace since the last '='
    code: List[str] = []     # code text since the last '=' (for field detection)
    frames: List[Optional[str]] = []  # one per open '{': dictionary name, or None

    def key_for(name: Optional[str]) -> Optional[str]:
        chain: List[str] = []
        for f in reversed(frames):
            if f is None:
                break
            chain.append(f)
        if not chain or not name:
            return None
        return ":".join(list(reversed(chain)) + [name])

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            code.append(c)
            i += 1
        elif c == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += text.count("\n", i, j)
            i = j
        elif c == "@":
            delim = "@@@" if text.startswith("@@@", i) else "@"
            start = i + len(delim)
            j = text.find(delim, start)
            if j < 0:
                break
            value = text[start:j]
            yield _Token("asset", value, line, field, None)
            line += value.count("\n")
            i = j + len(delim)
            after_eq = False
        elif c in "\"'":
            delim = c * 3 if text.startswith(c * 3, i) else c
            j = i + len(delim)
            while j < n and not text.startswith(delim, j):
                j += 2 if text[j] == "\\" else 1
            raw = text[i + len(delim):j]
            words = (field or "").split()
            name = words[-1] if after_eq and len(words) >= 2 else None
            yield _Token("string", _unescape_usd_string(raw), line, field, key_for(name))
            line += raw.count("\n")
            i = min(j + len(delim), n)
            after_eq = False
        elif c == "=":
            m = _FIELD_RE.search("".join(code).rsplit("\n", 1)[-1].split("(")[-1].split("{")[-1].split("}")[-1])
            field = " ".join(m.group(1).split()) if m and m.group(1).strip() else None
            code = []
            after_eq = True
            i += 1
        elif c == "{":
            words = (field or "").split()
            is_dict = after_eq and len(words) == 2 and words[0] == "dictionary"
            frames.append(words[1] if is_dict else None)
            code.append(c)
            after_eq = False
            i += 1
        elif c == "}":
            if frames:
                frames.pop()
            code.append(c)
            after_eq = False
            i += 1
        else:
            # Jump to the next character that matters (large layers are mostly numbers).
            m = _SPECIAL_RE.search(text, i + 1)
            j = m.start() if m else n
            chunk = text[i:j]
            if after_eq and chunk.strip():
                after_eq = False
            code.append(chunk[-256:])
            if len(code) > 64:
                del code[:32]
            i = j


def _find_script_paths(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "script_path":
                yield v
            else:
                yield from _find_script_paths(v)
    elif isinstance(value, list):
        for v in value:
            yield from _find_script_paths(v)


_SCRIPT_RE = re.compile(r'"script_path"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _script_paths_in(value: str) -> List[Any]:
    try:
        return list(_find_script_paths(json.loads(value)))
    except ValueError:  # not a JSON document; fall back to the "script_path": "..." pairs
        paths: List[Any] = []
        for g in _SCRIPT_RE.findall(value):
            try:
                paths.append(json.loads(f'"{g}"'))
            except ValueError:
                pass
        return paths


def _arc_targets(tokens: List[_Token], layer_dir: Path) -> Set[str]:
    """Files (normalized absolute paths) this layer composes via non-deleted sublayer/reference/
    payload arcs whose targets exist."""
    out: Set[str] = set()
    for t in tokens:
        if t.kind != "asset" or not t.field:
            continue
        words = t.field.split()
        if words[-1] not in ARC_FIELDS or words[0] == "delete":
            continue
        v = t.value.strip().replace("\\", "/")
        if not v or _DRIVE_RE.match(v) or v.startswith(("/", "~")) or ":" in v.split("/")[0]:
            continue
        target = os.path.normpath(os.path.join(str(layer_dir), v))
        if os.path.isfile(target):
            out.add(target)
    return out


def _check_usda_tokens(tokens: List[_Token], *, root: Path, layer_dir: Path, script_bases: Sequence[Path],
                       rel_file: str, has_lines: bool) -> List[PathIssue]:
    issues: List[PathIssue] = []
    for t in tokens:
        ln = t.line if has_lines else None
        if t.kind == "asset":
            issues += _check_path(t.value, prefix="PATH", root=root, bases=[layer_dir], rel_file=rel_file,
                                  line=ln, context=t.field, search_paths=True)
            continue
        if "script_path" in t.value and t.key is not None:
            current = t.key == BEHAVIOR_DATA_KEY
            paths = _script_paths_in(t.value)
            if not paths and current:
                issues.append(_issue("SCRIPT-PATH-JSON", rel_file, ln, "behavior data mentions script_path "
                                     "but no JSON script_path value was found", context=t.key))
            for sp in paths:
                if not isinstance(sp, str):
                    continue
                if not current:
                    issues.append(_issue("SCRIPT-PATH-LEGACY-KEY", rel_file, ln,
                                         f"script_path under customData key '{t.key}', which the extension "
                                         f"does not read (it reads '{BEHAVIOR_DATA_KEY}')", path=sp, context=t.key,
                                         fix="Move it to the current key or delete it"))
                issues += _check_path(sp, prefix="SCRIPT-PATH", root=root, bases=script_bases, rel_file=rel_file,
                                      line=ln, context=t.key, resolve=current)
            if paths:
                continue
        for m in _TEXT_ABS_RE.finditer(t.value):
            issues.append(_issue("TEXT-ABSOLUTE-PATH", rel_file, ln, "string value contains an absolute path",
                                 path=m.group(0), context=t.key or t.field, fix="Remove it or make it relative"))
    return issues


# --------------------------------------------------------------------------------------------
# pxr (optional)
# --------------------------------------------------------------------------------------------

_SDF: Any = None
_SDF_TRIED = False


def load_pxr(usd_libs: Optional[str] = None) -> Any:
    """Return ``pxr.Sdf`` or ``None``. ``usd_libs``: a folder containing the ``pxr`` package (e.g.
    Isaac Sim's ``extscache/omni.usd.libs-*``); it is added to ``sys.path`` and, on Windows, its
    ``bin`` folder to the DLL search path."""
    global _SDF, _SDF_TRIED
    if usd_libs:
        usd_libs = os.path.abspath(usd_libs)  # add_dll_directory rejects relative paths
        sys.path.insert(0, usd_libs)
        bin_dir = os.path.join(usd_libs, "bin")
        if hasattr(os, "add_dll_directory") and os.path.isdir(bin_dir):
            os.add_dll_directory(bin_dir)
        _SDF_TRIED = False
    if not _SDF_TRIED:
        _SDF_TRIED = True
        try:
            from pxr import Sdf  # type: ignore[import-not-found]
            _SDF = Sdf
        except Exception:  # ImportError, or a DLL load failure
            _SDF = None
    return _SDF


def _usd_kind(path: Path) -> str:
    """``text``, ``binary`` or ``other`` (usdz/unknown), by content (a ``.usd`` file can be either)."""
    if path.suffix.lower() == ".usdz":
        return "other"
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return "other"
    if head.startswith(b"#usda"):
        return "text"
    if head.startswith(b"PXR-USDC"):
        return "binary"
    return "other"


# --------------------------------------------------------------------------------------------
# Other formats
# --------------------------------------------------------------------------------------------

def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _check_xml(text: str, *, root: Path, layer_dir: Path, rel_file: str) -> List[PathIssue]:
    issues: List[PathIssue] = []
    for m in _XML_ATTR_RE.finditer(text):
        attr, value = m.group(1), html.unescape(m.group(3))
        lower = value.strip().lower()
        if attr.lower() == "filename" or lower.startswith(("file:", "package://")):
            issues += _check_path(value, prefix="PATH", root=root, bases=[layer_dir], rel_file=rel_file,
                                  line=_line_of(text, m.start()), context=f"{attr}=")
    return issues


def _check_text(text: str, *, rel_file: str) -> List[PathIssue]:
    return [_issue("TEXT-ABSOLUTE-PATH", rel_file, _line_of(text, m.start()), "text contains an absolute path",
                   path=m.group(0), fix="Remove it or make it relative")
            for m in _TEXT_ABS_RE.finditer(text)]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------

@dataclass
class _Layer:
    path: Path
    rel: str
    tokens: List[_Token]
    has_lines: bool
    arcs: Set[str] = dc_field(default_factory=set)


def iter_package_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _check_usd_stems(root: Path, usd_files: Sequence[Path]) -> List[PathIssue]:
    """USD-STEM-DUPLICATE: USD files in one folder whose names differ only in the extension (e.g.
    ``main.usd`` and ``main.usda``). Stems are compared case-insensitively, as the entry lookup and
    Windows file systems do. One issue per group, reported on the first file."""
    groups: Dict[Tuple[str, str], List[Path]] = {}
    for p in usd_files:
        groups.setdefault((str(p.parent), p.stem.lower()), []).append(p)
    issues: List[PathIssue] = []
    for (_, stem), files in sorted(groups.items()):
        if len(files) < 2:
            continue
        rels = sorted(f.relative_to(root).as_posix() for f in files)
        names = ", ".join(f.name for f in sorted(files))
        message = f"USD files share the name stem '{stem}' in one folder: {names}"
        if stem == ENTRY_STEM:
            message += "; the entry layer (main.*) is ambiguous"
        issues.append(_issue("USD-STEM-DUPLICATE", rels[0], None, message, path=", ".join(rels),
                             fix="Keep one of them; delete or rename the others and update references to them"))
    return issues


def _reachable(start: str, layers: Dict[str, _Layer]) -> Set[str]:
    seen: Set[str] = set()
    stack = list(layers[start].arcs)
    while stack:
        p = stack.pop()
        if p in seen or p not in layers:
            continue
        seen.add(p)
        stack.extend(layers[p].arcs)
    seen.discard(start)
    return seen


def check_package_paths(root: str | Path, *, use_pxr: bool = True) -> List[PathIssue]:
    """Check every stored path in the package at ``root``. Returns issues sorted by file and line.

    Binary USD layers are checked through ``pxr`` when it is importable (see :func:`load_pxr`) and
    ``use_pxr`` is true; otherwise each gets a ``LAYER-NOT-CHECKED`` info issue.

    Behavior ``script_path`` values resolve, at runtime, relative to the file of the prim's nearest
    payload (see the spec). Statically, a script path counts as resolved if it exists relative to
    the authoring layer or to any layer that layer composes (sublayers, references, payloads,
    transitively)."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"package root is not a directory: {root}")
    sdf = load_pxr() if use_pxr else None
    issues: List[PathIssue] = []
    layers: Dict[str, _Layer] = {}
    usd_files: List[Path] = []

    # Pass 1: tokenize layers, check the other formats.
    for path in iter_package_files(root):
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        if ext in USD_EXTS:
            usd_files.append(path)
        try:
            if ext in USD_EXTS:
                kind = _usd_kind(path)
                text: Optional[str] = None
                if kind == "text":
                    text = _read_text(path)
                elif kind == "binary" and sdf is not None:
                    layer = sdf.Layer.OpenAsAnonymous(str(path))
                    if layer is None:
                        issues.append(_issue("LAYER-UNREADABLE", rel, None, "pxr could not open the layer"))
                        continue
                    text = layer.ExportToString()
                else:
                    why = "binary layer and pxr is not available" if kind == "binary" else "unsupported layer format"
                    issues.append(_issue("LAYER-NOT-CHECKED", rel, None, f"not checked: {why}"))
                    continue
                tokens = list(_scan_usda(text))
                key = os.path.normpath(str(path))
                layers[key] = _Layer(path, rel, tokens, kind == "text", _arc_targets(tokens, path.parent))
            elif ext in XML_EXTS:
                issues += _check_xml(_read_text(path), root=root, layer_dir=path.parent, rel_file=rel)
            elif ext in TEXT_EXTS:
                issues += _check_text(_read_text(path), rel_file=rel)
        except Exception as e:  # one bad file must not stop the scan
            issues.append(_issue("LAYER-UNREADABLE", rel, None, f"could not check file: {type(e).__name__}: {e}"))
    issues += _check_usd_stems(root, usd_files)

    # Pass 2: check layers, with the composed layers' folders as extra script-path bases.
    for key, lay in layers.items():
        extra = sorted({os.path.dirname(p) for p in _reachable(key, layers)} - {str(lay.path.parent)})
        issues += _check_usda_tokens(lay.tokens, root=root, layer_dir=lay.path.parent,
                                     script_bases=[lay.path.parent] + [Path(d) for d in extra],
                                     rel_file=lay.rel, has_lines=lay.has_lines)
    return sorted(issues, key=lambda i: (i.file, i.line or 0, i.rule))


def has_errors(issues: List[PathIssue]) -> bool:
    return any(i.severity == Severity.ERROR for i in issues)


def summarize(issues: List[PathIssue]) -> Counter[str]:
    return Counter(i.rule for i in issues)


def format_issue(issue: PathIssue, *, with_fix: bool = False) -> str:
    """One line: ``file[:line]: severity RULE: message [path] (context)``, plus ``; fix: ...`` when
    ``with_fix`` and the issue has a fix."""
    loc = issue.file + (f":{issue.line}" if issue.line else "")
    out = f"{loc}: {issue.severity.value} {issue.rule}: {issue.message}"
    if issue.path is not None:
        out += f" [{issue.path}]"
    if issue.context:
        out += f" ({issue.context})"
    if with_fix and issue.fix:
        out += f"; fix: {issue.fix}"
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m amt.dtp.ise.lib.validation",
                                     description="Check that testbed/asset packages use portable, relative paths.")
    parser.add_argument("roots", nargs="*", metavar="DIR", help="package root directories")
    parser.add_argument("--json", action="store_true", help="print issues as JSON")
    parser.add_argument("--usd-libs", metavar="DIR", help="folder containing pxr (e.g. Isaac Sim's omni.usd.libs-*)")
    parser.add_argument("--no-pxr", action="store_true", help="don't check binary layers")
    parser.add_argument("--min-severity", choices=[s.value for s in Severity], default="info")
    parser.add_argument("--list-rules", action="store_true", help="print the rule catalog and exit")
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule, (sev, summary) in RULES.items():
            print(f"{rule:26} {sev.value:8} {summary}")
        return 0
    if not args.roots:
        parser.error("at least one DIR is required")
    if args.usd_libs and not args.no_pxr:
        load_pxr(args.usd_libs)

    min_rank = _SEVERITY_RANK[Severity(args.min_severity)]
    failed = False
    report: Dict[str, List[Dict[str, Any]]] = {}
    for root in args.roots:
        issues = check_package_paths(root, use_pxr=not args.no_pxr)
        failed |= has_errors(issues)
        shown = [i for i in issues if _SEVERITY_RANK[i.severity] >= min_rank]
        if args.json:
            report[root] = [i.model_dump(mode="json") for i in shown]
            continue
        print(f"== {root}")
        for i in shown:
            print("  " + format_issue(i))
        counts = Counter(i.severity.value for i in issues)
        print(f"  {counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} info")
    if args.json:
        print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

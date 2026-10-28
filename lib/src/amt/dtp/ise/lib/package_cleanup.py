"""Clean up common, mechanically fixable issues in a testbed or asset package.

Validation (``amt.dtp.ise.lib.validation``) only reads and reports; it never changes files. This module is
the separate, opt-in step that **does** change files, meant for the asset/testbed build or packaging
process (ADR-0020). Every fixer defaults to a dry run that reports what it would
change.

Fixers:

* ``fix_script_path_backslashes``: behavior ``script_path`` values written with Windows backslashes
  (``..\\_global\\disposer.py``) become forward slashes (``../_global/disposer.py``), which work on Windows
  and Linux. Only text layers (``.usda``) are edited, and only the ``script_path`` JSON value inside a
  single-quoted USD string; the rest of the file is left byte for byte.

CLI: ``python -m amt.dtp.ise.lib.package_cleanup <package dir> [--apply]`` (dry run without ``--apply``).
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .validation import SKIP_DIRS

# A "script_path": "<value>" pair as it appears in the raw text of a single-quoted USD string, where the
# JSON's own quotes are not escaped. The value is still in USD-escaped form.
_RAW_SCRIPT_PATH_RE = re.compile(r'"script_path"(\s*:\s*)"((?:[^"\\\n]|\\.)*)"')


@dataclass(frozen=True)
class Fix:
    """One change a fixer made (or would make, in a dry run)."""
    file: str           # relative to the package root, forward slashes
    line: int
    old: str            # the decoded value before
    new: str            # the decoded value after
    applied: bool

    def __str__(self) -> str:
        verb = "fixed" if self.applied else "would fix"
        return f"{self.file}:{self.line}: {verb} script_path {self.old!r} -> {self.new!r}"


def _usd_unescape(raw: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), raw)


def _usd_escape_single_quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _decode_value(raw: str) -> Optional[str]:
    """The script_path value as the extension sees it: USD-unescape, then JSON-decode; None if invalid."""
    try:
        return json.loads('"' + _usd_unescape(raw) + '"')
    except ValueError:
        return None


def _encode_value(value: str) -> str:
    """Back to raw text: JSON-encode (without the surrounding quotes), then USD-escape."""
    return _usd_escape_single_quoted(json.dumps(value)[1:-1])


def fix_script_path_backslashes(root: str | Path, *, apply: bool = False) -> List[Fix]:
    """Replace backslashes with forward slashes in behavior ``script_path`` values in every ``.usda`` file
    under ``root``. Returns the fixes (made when ``apply`` is True, otherwise only reported)."""
    root = Path(root)
    fixes: List[Fix] = []
    for path in sorted(root.rglob("*.usda")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        rel = path.relative_to(root).as_posix()
        file_fixes: List[Fix] = []

        def repl(m: re.Match) -> str:
            old = _decode_value(m.group(2))
            if old is None or "\\" not in old:
                return m.group(0)
            new = old.replace("\\", "/")
            line = text.count("\n", 0, m.start()) + 1
            file_fixes.append(Fix(rel, line, old, new, apply))
            return f'"script_path"{m.group(1)}"{_encode_value(new)}"'

        new_text = _RAW_SCRIPT_PATH_RE.sub(repl, text)
        if file_fixes and apply:
            path.write_bytes(new_text.encode("utf-8"))  # same bytes elsewhere, incl. newline style
        fixes.extend(file_fixes)
    return fixes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", help="the package (testbed or asset) directory")
    parser.add_argument("--apply", action="store_true", help="write the fixes (default: dry run)")
    args = parser.parse_args(argv)
    fixes = fix_script_path_backslashes(args.root, apply=args.apply)
    for fix in fixes:
        print(fix)
    print(f"{len(fixes)} script_path value(s) {'fixed' if args.apply else 'to fix (dry run; pass --apply)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

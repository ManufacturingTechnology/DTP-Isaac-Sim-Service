#!/usr/bin/env python3
"""Keep confidential material and NVIDIA-owned content out of the repository.

One policy, three callers:
  - the agent hook (`.claude/settings.json`, PreToolUse on Write/Edit/NotebookEdit): `--hook` reads the hook's
    JSON on stdin and blocks (exit 2) a write that would put a match into a tracked file;
  - `make content-check` (part of `make all`, so CI too): scans every tracked or to-be-tracked file;
  - the optional pre-commit hook (`.githooks/pre-commit`, enabled per clone with
    `git config core.hooksPath .githooks`): `--staged` scans the staged versions.

WHAT IT FLAGS
    Confidentiality stamps: generic document markings (the patterns in STAMPS below).
    Local denylist terms: `.claude/confidential-terms.txt` in this repo and `~/.claude/confidential-terms.txt`, both
        outside git. One term per line, `#` comments; a plain line matches case-insensitively as a literal, a line
        starting with `re:` is a regular expression. CI has neither file, so CI checks only the generic patterns.
    NVIDIA content: NVIDIA copyright notices and NVIDIA material files (`.mdl`) inside the repo. NVIDIA software and
        assets are referenced, never included (ADR-0025); a path or URL that points at them is fine and not flagged.

WHAT IT DOESN'T CHECK
    Binary files (USD crate files, images, CAD): their strings can't be matched reliably; the agent rules in AGENTS.md
    cover them. Text written by shell commands rather than the Write/Edit tools: the hook doesn't see it, but
    `make` and the pre-commit hook do.

A finding that is reviewed and fine goes into ALLOW (path -> reason), like REVIEWED in check-licenses.py.

Usage: check-content.py [--hook | --staged]
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Generic confidentiality markings. Case-sensitive where the marking is a stamp in capitals.
STAMPS = [
    ("confidentiality stamp", re.compile(r"\bCONFIDENTIAL\b")),
    ("confidentiality stamp", re.compile(r"(?i)\b(proprietary (and|&) confidential|confidential (and|&) proprietary)\b")),
    ("confidentiality stamp", re.compile(r"(?i)\binternal use only\b")),
    ("confidentiality stamp", re.compile(r"(?i)\bdo not (distribute|disclose|copy|reproduce)\b")),
    ("confidentiality stamp", re.compile(r"(?i)\b(under|subject to) (an? )?(NDA|non-disclosure agreement)\b")),
    ("confidentiality stamp", re.compile(r"(?i)\btrade secret\b")),
    ("export-control marking", re.compile(r"(?i)\bexport[- ]controlled\b|\bITAR\b")),
    ("paid standard's text", re.compile(r"(?i)\bcopyright protected document\b")),
]

NVIDIA = [
    ("NVIDIA copyright notice", re.compile(r"(?i)(copyright|SPDX-FileCopyrightText)[^\n]{0,80}\bnvidia\b")),
    ("NVIDIA copyright notice", re.compile(r"(?i)\bNVIDIA CORPORATION\b[^\n]{0,40}all rights reserved")),
]
NVIDIA_FILE_SUFFIXES = {".mdl": "NVIDIA material file (reference built-in MDLs by name, e.g. @OmniPBR.mdl@)"}

# Reviewed exceptions: repo-relative path -> reason. Keep this list short and explain each entry.
ALLOW = {}

# Files that spell the patterns themselves.
EXEMPT = {".github/scripts/check-content.py"}

DENYLISTS = [ROOT / ".claude" / "confidential-terms.txt", Path.home() / ".claude" / "confidential-terms.txt"]


def load_denylist():
    rules = []
    for path in DENYLISTS:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pattern = line[3:].strip() if line.startswith("re:") else re.escape(line)
            if not pattern:
                continue  # an empty pattern would match every write
            try:
                rules.append(("denylisted term", re.compile(pattern, re.IGNORECASE)))
            except re.error as error:
                print(f"check-content: skipping invalid denylist pattern in {path}: {error}", file=sys.stderr)
    return rules


def scan_text(text, rules):
    """Yield (line number, rule name, excerpt) for every match."""
    for name, pattern in rules:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            yield line, name, match.group(0)


def scan_file(rel, text, rules):
    if rel in EXEMPT or rel in ALLOW:
        return []
    findings = [(0, NVIDIA_FILE_SUFFIXES[s], "") for s in NVIDIA_FILE_SUFFIXES if rel.lower().endswith(s)]
    if text is not None:
        findings += list(scan_text(text, rules))
    return findings


def decode(data):
    """Text, or None for a binary file."""
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)


def report(findings):
    for rel, line, name, excerpt in findings:
        where = f"{rel}:{line}" if line else rel
        print(f"  {where}: {name}" + (f": {excerpt!r}" if excerpt else ""), file=sys.stderr)


def check_repo(staged):
    rules = STAMPS + NVIDIA + load_denylist()
    if staged:
        names = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").stdout.decode().split("\0")
    else:
        names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.decode().split("\0")
    findings = []
    for rel in filter(None, names):
        if staged:
            data = git("show", f":{rel}").stdout
        else:
            path = ROOT / rel
            if not path.is_file():
                continue  # deleted in the working tree
            data = path.read_bytes()
        findings += [(rel, *f) for f in scan_file(rel, decode(data), rules)]
    if findings:
        print("Content check FAILED: confidential or NVIDIA-owned material in files git would commit:", file=sys.stderr)
        report(findings)
        print("Remove it, or generalize it (AGENTS.md: 'Vendor-confidential material stays out'). A reviewed false "
              "positive goes into ALLOW in .github/scripts/check-content.py with a reason.", file=sys.stderr)
        return 1
    print("Content check passed.")
    return 0


def check_hook():
    """PreToolUse: block a Write/Edit/NotebookEdit that would put a match into a tracked file of this repo."""
    event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    tool_input = event.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return 0
    path = Path(event.get("cwd") or os.getcwd(), target).resolve()
    # The repository the file belongs to: this one, or a sibling (the testbeds repository) written from this session.
    parent = next((p for p in path.parents if p.is_dir()), None)
    top = git("rev-parse", "--show-toplevel", cwd=parent).stdout.decode().strip() if parent else ""
    if not top:
        return 0  # not in a git repository: the scratchpad, Claude's memory
    repo = Path(top).resolve()
    rel = path.relative_to(repo).as_posix()
    if git("check-ignore", "-q", "--no-index", rel, cwd=repo).returncode == 0:
        return 0  # gitignored (docs/src/Agents/Scratch/, local settings): private material may live there
    if repo != ROOT:
        rel = f"{repo.name}/{rel}"  # EXEMPT and ALLOW hold paths of this repository only
    texts = [tool_input.get(key) for key in ("content", "new_string", "new_source")]
    texts += [edit.get("new_string") for edit in tool_input.get("edits") or [] if isinstance(edit, dict)]
    text = "\n".join(t for t in texts if isinstance(t, str))
    findings = scan_file(rel, text, STAMPS + NVIDIA + load_denylist())
    if not findings:
        return 0
    print(f"Blocked by the content check: this write would put confidential or NVIDIA-owned material into the "
          f"tracked file {rel}:", file=sys.stderr)
    report([(rel, *f) for f in findings])
    print("Don't write it to a tracked file. Keep private material in docs/src/Agents/Scratch/ (gitignored) or write "
          "only a generalized conclusion without names, formats, or quotes (AGENTS.md, 'Vendor-confidential material "
          "stays out'). If you believe it's a false positive, stop and ask the user; a reviewed exception goes into "
          "ALLOW in .github/scripts/check-content.py.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if "--hook" in sys.argv[1:]:
        sys.exit(check_hook())
    sys.exit(check_repo(staged="--staged" in sys.argv[1:]))

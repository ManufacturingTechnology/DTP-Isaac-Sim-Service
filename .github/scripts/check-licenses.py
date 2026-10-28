#!/usr/bin/env python3
"""Fail the build when something we ship, or install by default, carries a license we haven't cleared.

Run by the root makefile (`make license-check`, part of `make all`), so a local build and CI both catch it.

WHAT IT CHECKS
    The dependency closure that a user gets by default from what this repo releases:
      - the lib wheel with its `server` extra (documented for hosting the control APIs),
      - the coprocessor wheel (default extras only: the opt-in `step` extra is excluded on purpose),
      - the extension's prebundled packages (extension/exts/amt.dtp.ise/config/requirements.txt).
    They are installed into a throwaway venv, and every installed distribution's declared license is read
    from its metadata (License-Expression, else License, else the "License ::" classifiers).

WHAT IT DOESN'T CHECK
    NVIDIA's runtime (Isaac Sim, Omniverse Kit, cuMotion): required, never shipped, users accept NVIDIA's terms.
    Dev and docs tooling (not shipped). Non-Python assets bundled in the built docs site (see the extension's
    THIRD-PARTY-NOTICES.md). Libraries a wheel bundles without declaring them: listed in REVIEWED below.

POLICY
    A license expression passes when every AND-term has at least one OR-alternative in ALLOWED. Anything else
    (GPL, AGPL, LGPL, SSPL, proprietary, unknown) fails, unless the package is in REVIEWED with a reason.
    Packages in FORBIDDEN fail whatever they declare (their metadata understates what the wheel contains).

Usage: check-licenses.py WHEEL_OR_REQUIREMENT... [-r REQUIREMENTS_FILE]...
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv

# Permissive licenses (SPDX ids, plus the classifier/free-text spellings packages actually use).
ALLOWED = {
    "MIT", "MIT-0", "MIT-CMU", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "Apache-2.0", "PSF-2.0",
    "Python-2.0", "CNRI-Python", "Zlib", "CC0-1.0", "Unlicense", "HPND",
    "MIT License", "The MIT License (MIT)", "BSD License", "Apache Software License", "Apache 2.0",
    "Python Software Foundation License", "ISC License (ISCL)", "zlib/libpng License",
}

# Reviewed exceptions: package -> reason. Keep this list short and explain each entry.
REVIEWED = {
    "certifi": "MPL-2.0 (weak, file-level copyleft): used unmodified, so no obligation reaches our code.",
    "numpy": "Wheels bundle libgfortran (GPL-3.0+ WITH GCC-exception-3.1) and libquadmath (LGPL-2.1+), "
             "loaded as separate libraries; standard for the scientific Python stack; we don't redistribute it.",
    "scipy": "Same bundled GCC runtime libraries as numpy.",
}

# Fail whatever the metadata says: the wheel bundles copyleft code its metadata doesn't declare.
FORBIDDEN = {
    "cascadio": "Its wheel bundles OpenCASCADE (LGPL-2.1 with an exception) but declares only MIT. Keep it behind "
                "the coprocessor's opt-in `step` extra.",
}

# Our own packages and the venv's tooling.
SKIP = {"amt-dtp-ise-lib", "amt-dtp-iscp", "pip", "setuptools", "wheel"}

INSPECT = r"""
import importlib.metadata as md, json
out = []
for d in md.distributions():
    m = d.metadata
    out.append({
        "name": m["Name"], "version": d.version,
        "expression": m.get("License-Expression") or "",
        "license": (m.get("License") or "").strip(),
        "classifiers": [c.split(" :: ")[-1] for c in (m.get_all("Classifier") or []) if c.startswith("License ::")],
    })
print(json.dumps(out))
"""


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared(dist: dict) -> str:
    """The license as declared: the SPDX expression if there is one, else a short License field, else the
    classifiers joined with OR (a package listing several license classifiers offers each of them)."""
    if dist["expression"]:
        return dist["expression"]
    lic = dist["license"]
    if lic and len(lic) <= 80 and "\n" not in lic:
        return lic
    classifiers = [c for c in dist["classifiers"] if c != "OSI Approved"]
    return " OR ".join(classifiers) if classifiers else (lic.splitlines()[0][:80] if lic else "")


def passes(expression: str) -> bool:
    if not expression:
        return False
    text = expression.replace("(", " ").replace(")", " ")
    for term in re.split(r"\s+AND\s+", text):
        alternatives = [a.strip() for a in re.split(r"\s+OR\s+", term) if a.strip()]
        if not any(a in ALLOWED for a in alternatives):
            return False
    return True


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 2
    workdir = tempfile.mkdtemp(prefix="license-check-")
    try:
        venv.create(workdir, with_pip=True)
        python = os.path.join(workdir, "Scripts", "python.exe") if os.name == "nt" else os.path.join(workdir, "bin", "python")
        print(f"license-check: installing {' '.join(argv)}", flush=True)
        subprocess.run([python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *argv], check=True)
        dists = json.loads(subprocess.run([python, "-c", INSPECT], check=True, capture_output=True, text=True).stdout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    failures, notes = [], []
    rows = []
    for dist in sorted(dists, key=lambda d: canonical(d["name"])):
        name = canonical(dist["name"])
        if name in SKIP:
            continue
        license_ = declared(dist)
        if name in FORBIDDEN:
            verdict = "FORBIDDEN"
            failures.append(f"{dist['name']} {dist['version']}: {FORBIDDEN[name]}")
        elif name in REVIEWED:
            verdict = "reviewed"
            notes.append(f"{dist['name']}: {REVIEWED[name]}")
        elif passes(license_):
            verdict = "ok"
        else:
            verdict = "NOT CLEARED"
            failures.append(f"{dist['name']} {dist['version']}: '{license_ or 'no license declared'}'")
        rows.append((dist["name"], dist["version"], license_ or "?", verdict))

    width = max(len(r[0]) + len(r[1]) for r in rows) + 2
    for name, version, license_, verdict in rows:
        print(f"  {name + ' ' + version:<{width}} {verdict:<12} {license_}")
    for note in notes:
        print(f"license-check: reviewed: {note}")
    if failures:
        print("\nlicense-check: FAILED. These would ship, or be installed by default, under a license that isn't cleared:",
              file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print("Make the dependency optional, replace it, or (after review) add it to REVIEWED in "
              ".github/scripts/check-licenses.py with the reason.", file=sys.stderr)
        return 1
    print(f"license-check: OK ({len(rows)} distributions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

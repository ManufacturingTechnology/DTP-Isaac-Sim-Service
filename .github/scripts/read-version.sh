#!/usr/bin/env bash
# Print the project's single source-of-truth version, validated as semver.
#
#     version=$(bash .github/scripts/read-version.sh)   # -> 0.1.0-alpha.1
#
# WHY THIS EXISTS
#     ci.yml and release.yml each parsed extension.toml with their own inlined
#     copy of
#
#         version=$(grep -m1 -oP '^version\s*=\s*"\K[^"]+' "$VERSION_FILE")
#
#     Two copies of a parse drift, and when they drift the git tag stops
#     agreeing with the payload. A common failure is an unanchored parse that
#     matches a docstring instead of the version line, which silently disables
#     the release guard. One anchored reader is the right number of readers.
#
# Diagnostics go to stderr; ONLY the version goes to stdout, so the caller can
# use $(...) safely.
set -uo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FILE="$ROOT/extension/exts/amt.dtp.ise/config/extension.toml"

die() { echo "::error::$1" >&2; exit 1; }

[ -f "$FILE" ] || die "version file not found: $FILE"

# Anchored to the start of a line, so a `version` substring anywhere else in the
# TOML (a dependency name, a comment, a description) cannot match.
#
# POSIX `sed`, deliberately NOT `grep -oP`: PCRE grep is not portable. Git Bash
# on Windows refuses it outright — "grep: -P supports only unibyte and UTF-8
# locales" — which would make this reader untestable on a dev machine even
# though it works on the runner. BRE costs nothing here.
mapfile -t matches < <(
    sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$FILE")

case "${#matches[@]}" in
    0) die "no '^version = \"...\"' assignment found in $FILE" ;;
    1) ;;
    # Ambiguity is a hard error, not first-match-wins: the makefiles derive
    # build_version from this same file with their own grep, so a second
    # assignment would let the git tag and the built artifacts disagree.
    *) die "expected exactly one top-level version assignment in $FILE, found ${#matches[@]}: ${matches[*]}" ;;
esac

version="${matches[0]}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    die "version '$version' is not valid semver (expected MAJOR.MINOR.PATCH[-PRERELEASE])"
fi

printf '%s\n' "$version"

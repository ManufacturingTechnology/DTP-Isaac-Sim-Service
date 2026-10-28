# hooks/nav_merge.py
"""Nav helpers for the docs build, plus the one-way link check.

``extra.nav_append``  — splice extra entries into an existing nav section (or the
root). Runs in ``on_config``.

``extra.nav_exclude`` — drop paths from the nav *and* from the build. The nav prune
runs in ``on_files`` at a LATE priority, which is the whole trick: the
``include_dir_to_nav`` plugin generates directory entries in its own ``on_files`` by
walking the filesystem, so anything pruning those entries must run afterwards.
Pruning in ``on_config`` cannot work — the entries do not exist yet.

Why ``nav_exclude`` rather than writing the nav out longhand: far fewer pages need
excluding than including, so include-by-default with exclude-by-exception is much
less maintenance — adding a folder under ``Agents/`` then needs no config change at all.

One-way link rule: user docs must never link into ``Agents/`` (the agent knowledge base),
because the public build excludes it and the link would be broken there. Agent pages may
link to user docs. ``on_page_markdown`` warns on any violation, in both builds; run
``mkdocs build --strict`` to make the warning fail the build.
"""

import logging
import re

from mkdocs.plugins import event_priority

log = logging.getLogger("mkdocs.hooks.nav_merge")

# A markdown link or image target that resolves into the Agents/ tree: ](Agents/...),
# ](../Agents/...), ](../../Agents/...), with or without a leading ./
_AI_LINK = re.compile(r"\]\(\s*(?:\./)?(?:\.\./)*Agents/")

# Absolute, user-specific paths (privacy): Windows and WSL user folders, Linux and macOS home folders.
_ABS_PATH = re.compile(r"[A-Za-z]:[\\/]Users[\\/]|/(?:home|Users)/[^/\s]+/|/mnt/[a-z]/Users/", re.I)


def _excluded_prefixes(config):
    return [str(p).replace("\\", "/").lstrip("./")
            for p in (config.extra.get("nav_exclude") or [])]


# --------------------------------------------------------------- nav_append
def _splice(items, additions):
    for item in items:
        if isinstance(item, dict):
            (title, value), = item.items()
            if isinstance(value, list):
                if title in additions:
                    value.extend(additions.pop(title))
                _splice(value, additions)


def on_config(config, **kwargs):
    # nav_exclude paths are excluded from the BUILD too, not just the nav -- an
    # agent-only page that still rendered would just be an unlinked live URL.
    # mkdocs compiles exclude_docs into a PathSpec at load time, so extend that
    # rather than the raw string.
    patterns = _excluded_prefixes(config)
    if patterns:
        import pathspec

        # 'gitignore' is the non-deprecated name for the gitwildmatch factory.
        add = pathspec.PathSpec.from_lines("gitignore", patterns)
        existing = config.exclude_docs
        config.exclude_docs = (
            pathspec.PathSpec(list(existing.patterns) + list(add.patterns))
            if existing else add
        )

    additions = config.extra.get("nav_append")
    if not additions or not config.nav:
        return config

    additions = dict(additions)          # don't mutate the loaded config
    root = additions.pop("__root__", [])
    _splice(config.nav, additions)
    config.nav.extend(root)

    if additions:                        # typo protection
        raise ValueError(f"nav_append: unknown section(s) {list(additions)}")
    return config


# --------------------------------------------------------------- nav_exclude
def _prune(items, prefixes):
    """Drop nav entries whose target starts with an excluded prefix.

    Sections left empty are dropped as well, so excluding a whole directory removes
    its heading instead of leaving a dead one behind.
    """
    kept = []
    for item in items:
        if isinstance(item, str):
            if not any(item.replace("\\", "/").startswith(p) for p in prefixes):
                kept.append(item)
        elif isinstance(item, dict):
            (title, value), = item.items()
            if isinstance(value, list):
                sub = _prune(value, prefixes)
                if sub:
                    kept.append({title: sub})
            elif isinstance(value, str):
                if not any(value.replace("\\", "/").startswith(p) for p in prefixes):
                    kept.append(item)
            else:
                kept.append(item)
        else:
            kept.append(item)
    return kept


@event_priority(-100)      # must run AFTER include_dir_to_nav's on_files
def on_files(files, config, **kwargs):
    prefixes = _excluded_prefixes(config)
    if prefixes and config.nav:
        config.nav[:] = _prune(config.nav, prefixes)
    return files


# --------------------------------------------------------------- one-way links
def on_page_markdown(markdown, page, **kwargs):
    src = page.file.src_uri.replace("\\", "/")
    if not src.startswith("Agents/") and _AI_LINK.search(markdown):
        log.warning(f"{src}: user docs must not link into Agents/ (the agent knowledge base is "
                    f"excluded from the public build); move the content or drop the link")
    if _ABS_PATH.search(markdown):
        log.warning(f"{src}: contains an absolute, user-specific path; use a repo-relative path "
                    f"(privacy rule, see AGENTS.md)")
    return markdown

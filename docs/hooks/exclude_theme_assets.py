# hooks/exclude_theme_assets.py
"""Keep theme assets the site never uses out of the build.

Material for MkDocs ships search support for non-English languages in ``assets/javascripts/lunr/``
(lunr-languages: MPL-1.1; wordcut: LGPL; TinySegmenter), and MkDocs copies every theme file into the
site. The search worker loads those files only for languages other than English, so an English-only
site never uses them. Dropping them from the file collection in ``on_files`` means they are never
written to the site, so the site doesn't distribute copyleft code it doesn't run.

MkDocs adds theme files to the collection before the ``on_files`` event (``mkdocs/commands/build.py``),
so removing them here is enough. If the site's search languages ever include anything but English,
the files are kept (and must then be listed in ``docs/THIRD-PARTY-NOTICES.md``).
"""

LUNR_LANGUAGE_ASSETS = "assets/javascripts/lunr/"


def _search_languages(config):
    for name in ("material/search", "search"):
        plugin = config.plugins.get(name)
        if plugin is not None:
            lang = plugin.config.get("lang") or config.theme.get("language") or "en"
            return [lang] if isinstance(lang, str) else list(lang)
    return []


def on_files(files, config, **kwargs):
    languages = _search_languages(config)
    if languages and all(lang == "en" for lang in languages):
        for file in [f for f in files if f.src_uri.startswith(LUNR_LANGUAGE_ASSETS)]:
            files.remove(file)
    return files

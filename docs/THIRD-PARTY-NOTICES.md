# Third-Party Notices: the documentation site

The documentation's own content is licensed under the Apache License 2.0 (`LICENSE.TXT`; attribution in `NOTICE`).
The docs build copies this file, `LICENSE.TXT`, and `NOTICE` into the built site (the documentation zip, the
extension zip's `docs/`, and the published site).

## Included in the built site

The site is built with Material for MkDocs. Its theme files, and the libraries compiled into them, are part of every
built page:

| Component | License | Copyright / source |
|---|---|---|
| Material for MkDocs 9.5.49 (theme CSS, JavaScript, templates) | MIT | Copyright (c) 2016-2024 Martin Donath; https://github.com/squidfunk/mkdocs-material |
| lunr (in the search worker) | MIT | Oliver Nightingale; https://github.com/olivernn/lunr.js |
| clipboard (in the theme bundle) | MIT | https://github.com/zenorocha/clipboard.js |
| escape-html (in the theme bundle) | MIT | Copyright (c) 2012-2013 TJ Holowaychuk, (c) 2015 Andreas Lubbe, (c) 2015 Tiancheng "Timothy" Gu; https://github.com/component/escape-html |
| focus-visible (in the theme bundle) | W3C Software and Document License | https://github.com/WICG/focus-visible |
| RxJS (in the theme bundle) | Apache-2.0 | https://github.com/ReactiveX/rxjs |
| tslib (in the theme bundle) | 0BSD | Copyright (c) Microsoft Corporation; https://github.com/microsoft/tslib |
| material-design-color (compiled into the theme stylesheet) | MIT | https://www.npmjs.com/package/material-design-color |
| Roboto and Roboto Mono fonts (`assets/external/`, downloaded from Google Fonts at build time by the theme's `privacy` plugin) | SIL Open Font License 1.1 | Copyright 2011 Google Inc.; https://fonts.google.com/specimen/Roboto, https://fonts.google.com/specimen/Roboto+Mono |
| Mermaid 11 (`assets/external/`, downloaded at build time; renders the diagrams) | MIT | Copyright (c) 2014-2024 Knut Sveidqvist; https://github.com/mermaid-js/mermaid |
| iframe-worker shim (`assets/external/`, downloaded at build time; lets the search worker run from `file://`) | MIT | Copyright (c) Martin Donath; https://github.com/squidfunk/iframe-worker |
| Material Design Icons (icons used in the pages) | Apache-2.0 (Pictogrammers Free License) | https://pictogrammers.com |
| Octicons (icons used in the pages) | MIT | Copyright (c) 2024 GitHub Inc.; https://github.com/primer/octicons |
| Font Awesome Free (icons used in the pages) | Icons: CC BY 4.0 | Copyright (c) 2024 Fonticons, Inc.; https://fontawesome.com (each icon carries its attribution) |

The MIT License, as it applies to the MIT-licensed components above (with each component's copyright notice):

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Excluded at build time: the theme's search support for non-English languages (lunr-languages, MPL-1.1; wordcut, LGPL;
TinySegmenter). The site is English-only, so the search never loads them, and `hooks/exclude_theme_assets.py` keeps
them out of the build.

The site also ships the theme's source maps (`*.map`), which carry the source of the bundled libraries above under the
same licenses.

## Referenced, not included

The `privacy` plugin self-hosts the fonts, Mermaid and the worker shim (above), so a page view contacts only the site
itself, with two exceptions: the theme calls the GitHub API for the repository badge, and the copy of the site inside the
extension zip (a different origin) loads Mermaid from the public site when it renders a diagram.

| Component | How it is used | License |
|---|---|---|
| resize-observer-polyfill | loaded from a CDN by the theme when the browser needs it | MIT |
| GitHub REST API | called by the theme when a page is viewed, to show the repository badge (`repo_url`); no code is loaded | n/a |
| MkDocs, Material for MkDocs (the Python package), and the plugins in `requirements.txt` | build tools; only the theme assets above end up in the site | BSD-2-Clause, MIT, Apache-2.0 |

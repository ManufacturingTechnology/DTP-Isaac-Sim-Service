# The version comes from ONE reader, shared with the CI workflows, instead of a
# per-makefile `grep "version"` -- that grep was unanchored, so any line that
# merely CONTAINED the word (a comment, a description, a dependency name) would
# have silently corrupted every artifact filename. See the script for details.
#
# `:=` not `=`: expand once, so the guard below is meaningful and the reader is
# not re-run on every reference.
build_version := $(shell bash .github/scripts/read-version.sh)
ifeq ($(strip $(build_version)),)
$(error could not read the version -- run `bash .github/scripts/read-version.sh` to see why)
endif

# Every target rebuilds its artifact on each run: the artifacts depend on many source files that file-based rules
# don't track, so "build only if missing" shipped stale content. The one reuse: the extension build reuses an
# existing coprocessor wheel (`make -C coprocessor clean` forces a rebuild).
.PHONY: all docs-zip extension-zip lib-wheel clean distclean
all: docs-zip extension-zip lib-wheel license-check content-check

# License check, after the artifacts exist: everything we ship, or install by default (the lib wheel with its
# `server` extra, the coprocessor wheel without opt-in extras, the extension's prebundled packages), must carry
# a cleared license. Part of `all`, so a local build fails the same way CI does. Policy and reviewed exceptions:
# .github/scripts/check-licenses.py.
.PHONY: license-check
license-check: extension-zip lib-wheel
	python3 .github/scripts/check-licenses.py "$$(ls dist/amt_dtp_ise_lib-*.whl)[server]" "$$(ls coprocessor/dist/*.whl)" \
		-r extension/exts/amt.dtp.ise/config/requirements.txt

# Content check: no confidentiality stamps, locally denylisted terms, or NVIDIA-owned content (copyright notices,
# .mdl files) in any file git would commit. Part of `all`, so CI runs it too (without the local denylist, which
# isn't in git). Agents get the same check as a hook before every file write. Policy: .github/scripts/check-content.py.
.PHONY: content-check
content-check:
	python3 .github/scripts/check-content.py

# Test suites, each against its freshly built wheel in a throwaway venv: the lib (lib/tests, with the `server`
# extra) and the co-processor (coprocessor/tests). ci.yml runs this as a REQUIRED status check. The extension needs
# Isaac Sim and has no automated tests; the lib's offline USD tests skip without `pxr`.
.PHONY: test
test:
	$(MAKE) -C lib test
	$(MAKE) -C coprocessor test

# `clean` removes build outputs (the venvs are kept and only updated when their dependency files change);
# `distclean` also removes the venvs and the co-processor wheel, for a completely fresh build.
clean:
	rm -rf dist/
	$(MAKE) -C extension clean
	$(MAKE) -C docs clean
	$(MAKE) -C lib clean

distclean: clean
	$(MAKE) -C docs distclean
	$(MAKE) -C lib distclean
	$(MAKE) -C coprocessor distclean


# Docs (built first: the extension embeds the site)
docs-zip:
	$(MAKE) -C docs
	mkdir -p dist/
	rm -f dist/amt_dtp_ise_docs-*.zip
	cd docs/dist/ && zip -r ../../dist/amt_dtp_ise_docs-$(build_version).zip *


# Extension (embeds the docs site built above; its build cleans lib/, so the lib wheel is built after it)
extension-zip: docs-zip
	$(MAKE) -C extension DOCS_BUILT=1
	mkdir -p dist/
	rm -f dist/amt_dtp_ise-*.zip
	cp extension/dist/amt_dtp_ise-*.zip dist/


# Python Library
lib-wheel: extension-zip
	$(MAKE) -C lib
	mkdir -p dist/
	rm -f dist/amt_dtp_ise_lib-*.whl
	cp lib/dist/*.whl dist/

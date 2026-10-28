# The version comes from ONE reader, shared with the CI workflows, instead of a
# per-makefile `grep "version"` -- that grep was unanchored, so any line that
# merely CONTAINED the word (a comment, a description, a dependency name) would
# have silently corrupted every artifact filename. See the script for details.
#
# `:=` not `=`: expand once, so the guard below is meaningful and the reader is
# not re-run on every reference.
build_version := $(shell bash ../.github/scripts/read-version.sh)
ifeq ($(strip $(build_version)),)
$(error could not read the version -- run `bash ../.github/scripts/read-version.sh` to see why)
endif

# Always rebuild: the site depends on every page, so a file-based rule would serve a stale site after edits.
.PHONY: all build dev clean distclean
all: build

build: venv-linux/touchfile
	rm -rf dist/
	export SITE_NAME="AMT DTP Isaac Sim Service (ISS) v$(build_version)" && \
	echo "SITE_NAME: $${SITE_NAME}" && \
	venv-linux/bin/mkdocs build --site-dir dist/
#	Ship the repo's license, NOTICE, and third-party notices with the site
	cp ../LICENSE.TXT ../NOTICE THIRD-PARTY-NOTICES.md dist/

# `make dev` serves the site WITH the agent knowledge base (docs/src/Agents/); `make` builds the
# public site without it. It binds 127.0.0.1, not 0.0.0.0: mkdocs writes the serve address into script URLs, and
# browsers refuse http://0.0.0.0/..., which stops Material's JavaScript (sidebars, search). WSL forwards 127.0.0.1 to
# Windows.
dev: venv-linux/touchfile
	export SITE_NAME="AMT DTP Isaac Sim Service (ISS) v$(build_version)" && \
	echo "SITE_NAME: $${SITE_NAME}" && \
	venv-linux/bin/mkdocs serve --livereload -a 127.0.0.1:4000 -f mkdocs.dev.yml
	
# `clean` removes the built site; `distclean` also removes the venvs (kept between builds and only updated when
# requirements.txt changes). Venvs are per OS (ADR-0029): venv-linux/ here, venv-windows/ for the .bat scripts.
clean:
	@rm -rf dist/

distclean: clean
#	Both OSes' venvs (ADR-0029: distclean leaves no venv; distclean.bat does the same from Windows)
	@rm -rf venv-linux/ venv-windows/
#	The venv from before the per-OS names (ADR-0029); drop this once no clone has it
	@rm -rf venv/

# Update venv-linux when requirements.txt changes
venv-linux/touchfile: requirements.txt
# 	Create a virtual environment if it doesn't exist
	test -d venv-linux || python3 -m venv venv-linux
#	Install python dependencies from requirements.txt
	. venv-linux/bin/activate; pip install -Ur requirements.txt
#	Signal that venv-linux has been updated for due to requirements.txt change
	touch venv-linux/touchfile

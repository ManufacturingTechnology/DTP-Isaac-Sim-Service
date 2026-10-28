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
# lib_src_rel is relative to lib_dst
lib_src = ../lib/src/amt/dtp/ise/lib/
lib_src_rel = ../../../../$(lib_src)
lib_dst = src/amt/dtp/iscp/lib

# Venvs are per OS (ADR-0029): venv-linux/ here, venv-windows/ for the .bat scripts.
# `clean` removes build outputs; `distclean` also removes the venvs. The venvs are kept between builds and only
# updated (venv-linux/touchfile reinstalls when the dependency file changes), so a build doesn't reinstall everything.
.PHONY: all build dev run test clean distclean
all: build

build: clean venv-linux/touchfile
#	Copy library files into this package
	rsync -av --exclude='__pycache__' "$(lib_src)" "$(lib_dst)"
#	Create version file
	echo "$(build_version)" > VERSION
#	Push the version to the library
	echo "__version__ = \"$(build_version)\"" > src/amt/dtp/iscp/__init__.py
#	Ship the repo's license and NOTICE in the package (listed in pyproject.toml `license-files`)
	cp ../LICENSE.TXT ../NOTICE .
#	Build the python package
	venv-linux/bin/python -m build
#	Remove version file and the license copies
	rm VERSION LICENSE.TXT NOTICE
#	Clean the library
	echo "__version__ = \"0.0.0\"" > src/amt/dtp/iscp/__init__.py

dev: venv-linux/touchfile
#	To keep files synched for development purposes, create a symbolic link to the library, unless one exists (from an
#	earlier run, or dev.bat's junction): `ln -s` onto an existing link to a directory creates a link inside the lib
	test -e "$(lib_dst)" || ln -s "$(lib_src_rel)" "$(lib_dst)"

run: venv-linux/touchfile
	venv-linux/bin/python -m src.amt.dtp.iscp.coprocessor

# Test the built wheel, not the source tree, in its own venv (venv-linux-test; `run` starts the server itself)
test: build
	test -d venv-linux-test || python3 -m venv venv-linux-test
	venv-linux-test/bin/pip install "$$(ls dist/*.whl)" pytest
#	Same version number, new contents: reinstall our package itself (the test venv is kept between runs)
	venv-linux-test/bin/pip install --force-reinstall --no-deps "$$(ls dist/*.whl)"
	venv-linux-test/bin/pytest tests

clean:
#	Recursively remove pycache files
	find . -type d -name "__pycache__" -exec rm -rf {} +
#	Recursively remove pytest caches files
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
#	Try file (symlink) else try directory
	rm -f "$(lib_dst)" || rm -rf "$(lib_dst)"
	rm -rf src/*.egg-info
	rm -rf dist/

distclean: clean
#	Both OSes' venvs (ADR-0029: distclean leaves no venv; distclean.bat does the same from Windows)
	rm -rf venv-linux-test/ venv-linux/
	rm -rf venv-windows-test/ venv-windows/
#	Venvs from before the per-OS names (ADR-0029); drop these once no clone has them
	rm -rf venv-test/ venv/

# Update venv-linux when pyproject.toml changes
venv-linux/touchfile: pyproject.toml
	echo "Setting up virtual environment..."
#	Create a virtual environment if it doesn't exist
	test -d venv-linux || python3 -m venv venv-linux
#	Install package with regular and dev dependencies from pyproject.toml
	venv-linux/bin/pip install -e ".[dev]"
#	Signal that venv-linux has been updated due to pyproject.toml change
	touch venv-linux/touchfile

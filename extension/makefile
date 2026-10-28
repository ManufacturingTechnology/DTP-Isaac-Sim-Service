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
build_basename = amt_dtp_ise-$(build_version)
# Library source (master copy) and destination
# lib_src_rel is relative to lib_dst
lib_src = ../lib/src/amt/dtp/ise/lib
lib_src_rel = ../../../../../$(lib_src)
lib_dst = exts/amt.dtp.ise/amt/dtp/ise/lib
# coprocessor source (master copy) and destination
# coproc_src_rel is relative to coproc_dst
coproc_src = ../coprocessor
coproc_src_rel = ../../$(coproc_src)
coproc_dst = exts/amt.dtp.ise/coprocessor
# docs source (master copy) and destination
# docs_src_rel is relative to docs_dst
docs_src = ../docs/dist
docs_src_rel = ../../$(coproc_src)
docs_dst = exts/amt.dtp.ise/docs

# `build` always rebuilds (it starts with `clean`); the only reuse is the coprocessor wheel, below.
.PHONY: all build dev clean package-prebuilt-venv
all: build

build: clean exts/amt.dtp.ise/pip_prebundle
#	Clean the lib before building
	cd ../lib && make clean
#	Copy the library into the extension and clear its version
	rsync -av --exclude='__pycache__' "$(lib_src)/" "$(lib_dst)"
	echo "" > "$(lib_dst)/__init__.py"
#	Set the extension version for python
	sed -i 's/__version__[[:space:]]*=[[:space:]]*".*"/__version__ = "$(build_version)"/g' "exts/amt.dtp.ise/amt/dtp/ise/__version__.py"
#	Create the cosim directory
	mkdir "$(coproc_dst)"
#	Build the coprocessor only if its wheel doesn't exist yet; otherwise reuse it (assumed up to date;
#	`make -C ../coprocessor clean` forces a rebuild)
	if [ ! -f ../coprocessor/dist/*.whl ]; then \
		cd "../coprocessor" && make; \
	fi
#	Rebuild the documentation every time (the root makefile passes DOCS_BUILT=1 after building it itself)
ifndef DOCS_BUILT
	$(MAKE) -C ../docs
endif
#	Copy the library into the extension and clear its version
	rsync -av --exclude='__pycache__' "$(docs_src)/" "$(docs_dst)"
#	Copy the wheels into the coprocessor directory
	cp ../coprocessor/dist/*.whl "$(coproc_dst)/"
#	Copy installer files into the coprocessor directory
	cp ../coprocessor/install/* "$(coproc_dst)/"
#	Ship the repo's license, NOTICE, and third-party notices with the extension
	cp ../LICENSE.TXT ../NOTICE THIRD-PARTY-NOTICES.md exts/amt.dtp.ise/
	cp ../coprocessor/THIRD-PARTY-NOTICES.md "$(coproc_dst)/"
#	Make the directory for output files
	mkdir -p "dist"
#	Build the linux zip file, excluding pycache directories
	cd "exts" && zip -9 -q -r "../dist/$(build_basename).zip" amt.dtp.ise -x "*__pycache__*" -x "*.gitignore*" -x "*requirements.txt*"
#	No coprocessor venv is created here: install.bat / install.sh create it on the coprocessor's first start (ADR-0030)
#	Reset the extension version
	sed -i 's/__version__[[:space:]]*=[[:space:]]*".*"/__version__ = "0.0.0"/g' "exts/amt.dtp.ise/amt/dtp/ise/__version__.py"

# EXPERIMENTAL, not used for releases (ADR-0030): package the extension with the coprocessor's venv pre-built. Run only
# by build-prebuilt-venv.bat (after `build` and install.bat), never by `build` or the root makefile. Kept so the idea
# can be revisited; before it can be used it needs the venvs installed before zipping, per-OS zip names, and a way to
# handle the absolute Python path every venv records (it may not exist on the user's machine).
package-prebuilt-venv:
#	Make the directory for output files
	mkdir -p "dist"
#	Set the extension version for python
	sed -i 's/__version__[[:space:]]*=[[:space:]]*".*"/__version__ = "$(build_version)"/g' "exts/amt.dtp.ise/amt/dtp/ise/__version__.py"
#	Install the linux venv
#	$(coproc_dst)/install.sh
#	Build the linux zip file, excluding pycache directories
#	cd "exts" && zip -9 -q -r "../dist/$(build_basename)-linux.zip" amt.dtp.ise -x "*__pycache__*" -x "*.gitignore*" -x "*requirements.txt*"
#	Build the linux+windows zip file, excluding pycache directories
#	cd "exts" && zip -9 -q -r "../dist/$(build_basename)-linux+windows.zip" amt.dtp.ise -x "*__pycache__*" -x "*.gitignore*" -x "*requirements.txt*"
#	Remove the linux venv
	rm -rf $(coproc_dst)/venv-linux/
#	Build the windows zip file, excluding pycache directories
#	cd "exts" && zip -9 -q -r "../dist/$(build_basename)-windows.zip" amt.dtp.ise -x "*__pycache__*" -x "*.gitignore*" -x "*requirements.txt*"
#	Reset the extension version
	sed -i 's/__version__[[:space:]]*=[[:space:]]*".*"/__version__ = "0.0.0"/g' "exts/amt.dtp.ise/amt/dtp/ise/__version__.py"


dev: clean exts/amt.dtp.ise/pip_prebundle
#	To keep files synched for development purposes, create a symbolic link to the coprocessor
	ln -s "$(lib_src_rel)" "$(lib_dst)"
	ln -s "$(coproc_src_rel)" "$(coproc_dst)"
#	Set the coprocessor up for development
	cd "$(coproc_dst)" && make dev

exts/amt.dtp.ise/pip_prebundle: exts/amt.dtp.ise/config/requirements.txt
#	Install python pip dependencies in prebundled virtual environment
	mkdir -p "exts/amt.dtp.ise/pip_prebundle/"
	python3 -m pip install -r "exts/amt.dtp.ise/config/requirements.txt" --target "exts/amt.dtp.ise/pip_prebundle"

clean:
#	Recursively remove pycache files
	find . -type d -name "__pycache__" -exec rm -rf {} +
#	Recursively remove pytest caches files
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
#	Remove the library
	rm -f "$(lib_dst)" || rm -rf "$(lib_dst)"
#	Remove the coprocessor
	rm -f "$(coproc_dst)" || rm -rf "$(coproc_dst)"
#	Remove the docs
	rm -f "$(docs_dst)" || rm -rf "$(docs_dst)"
#	Remove prebundled virtual environment
	rm -rf "exts/amt.dtp.ise/pip_prebundle/"
#	Remove the copied license files
	rm -f exts/amt.dtp.ise/LICENSE.TXT exts/amt.dtp.ise/NOTICE exts/amt.dtp.ise/THIRD-PARTY-NOTICES.md
#	Remove existing build files
	rm -rf "dist/"
	rm -rf src/*.egg-info


all: venv/touchfile
	export SITE_NAME="AMT DTP Isaac Sim Service (ISS)" && \
	echo "SITE_NAME: $${SITE_NAME}" && \
	venv/bin/mkdocs build --site-dir dist/

dev: venv/touchfile
	export SITE_NAME="AMT DTP Isaac Sim Service (ISS)" && \
	echo "SITE_NAME: $${SITE_NAME}" && \
	venv/bin/mkdocs serve --livereload -a localhost:4000
	
clean:
	@rm -rf dist/
	@rm -rf venv/

venv/touchfile: requirements.txt
	test -d venv || python3 -m venv venv
	. venv/bin/activate; pip install -Ur requirements.txt
	touch venv/touchfile

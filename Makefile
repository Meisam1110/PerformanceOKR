# Convenience targets. None of these are required: `python run.py` installs
# what it needs and starts the app on its own.

PYTHON ?= python3

.PHONY: help run install test build app clean

help:
	@echo "make run      start the tracker (installs dependencies on first run)"
	@echo "make install  create .venv and install dependencies without starting"
	@echo "make test     run the full test suite"
	@echo "make build    rebuild the front end from vendor/OKR_Tracker.source.html"
	@echo "make app      build a standalone executable into dist/"
	@echo "make clean    remove .venv, build output and caches"

run:
	$(PYTHON) run.py

install:
	$(PYTHON) -c "import pathlib, sys; sys.path.insert(0, '.'); \
	from okr_tracker.bootstrap import create_venv, install_dependencies; \
	i = create_venv(pathlib.Path('.venv')); \
	install_dependencies(i, pathlib.Path('requirements.txt'))"
	@echo "Done. Start it with: make run"

test:
	$(PYTHON) tests/run_all.py

build:
	$(PYTHON) tools/build_frontend.py

app:
	$(PYTHON) -m pip install pyinstaller
	$(PYTHON) -m PyInstaller packaging/okr-tracker.spec

clean:
	rm -rf .venv build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

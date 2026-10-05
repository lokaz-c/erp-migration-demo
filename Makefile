# Synthetic book generator and supplier-matching calibration.
PYTHON ?= python3.11
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: install data calibrate test lint format

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"
	touch $@

install: $(VENV)/.installed

data: install ## write synthetic workbooks and ground truth to data/
	$(PY) -m erp_migration generate

calibrate: install ## print the supplier-matching threshold sweep
	$(PY) -m erp_migration calibrate

test: install
	$(PY) -m pytest

lint: install
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format: install
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

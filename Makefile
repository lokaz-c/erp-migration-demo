# Generate books (`make data`), start PostgreSQL (`make db`), load them (`make etl`).
PYTHON ?= python3.11
VENV := .venv
PY := $(VENV)/bin/python
DATABASE_URL ?= postgresql://erp:erp@localhost:54329/erp
export DATABASE_URL

.PHONY: install db data etl calibrate test lint format db-down db-reset

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"
	touch $@

install: $(VENV)/.installed

db: ## start PostgreSQL 18 in Docker and wait until it is healthy
	docker compose up -d --wait db

data: install ## write synthetic workbooks and ground truth to data/
	$(PY) -m erp_migration generate

etl: install ## load data/books into PostgreSQL
	$(PY) -m erp_migration etl

calibrate: install ## print the supplier-matching threshold sweep
	$(PY) -m erp_migration calibrate

test: install ## unit tests plus database tests (starts a throwaway container)
	$(PY) -m pytest

lint: install
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format: install
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

db-down:
	docker compose down

db-reset: ## drop the database volume
	docker compose down -v

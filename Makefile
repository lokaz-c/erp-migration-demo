# One-command demo: `make demo` (needs Docker and Python 3.11+).
PYTHON ?= python3.11
VENV := .venv
PY := $(VENV)/bin/python
DATABASE_URL ?= postgresql://erp:erp@localhost:54329/erp
export DATABASE_URL

.PHONY: demo install db data etl report calibrate test lint format db-down db-reset

demo: install ## fresh database, generate books, load twice (second load is a no-op), report
	docker compose down -v --remove-orphans
	docker compose up -d --wait db
	$(PY) -m erp_migration generate
	$(PY) -m erp_migration etl
	$(PY) -m erp_migration etl
	$(PY) -m erp_migration report

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

report: install ## write docs/index.html and refresh the README results block
	$(PY) -m erp_migration report

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

VENV := .venv
BIN := $(VENV)/bin
# 9 MB
MAX_REPO_KB := 9216

.PHONY: install dev test lint format typecheck audit size-check

$(BIN)/python:
	python3 -m venv $(VENV)

install: $(BIN)/python
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

dev:
	$(BIN)/uvicorn app.main:app --reload

test:
	$(BIN)/pytest -q

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

typecheck:
	$(BIN)/mypy app

audit:
	$(BIN)/pip-audit --skip-editable

# Fails if either the working tree (tracked + unignored files) or git history exceeds 9 MB.
size-check:
	@tree=$$(git ls-files -co --exclude-standard -z | xargs -0 -r du -ck | tail -1 | cut -f1); \
	hist=$$(git count-objects -v | awk '/^size(-pack)?:/ {s += $$2} END {print s + 0}'); \
	echo "Working tree: $${tree:-0} KB, git objects: $${hist} KB (limit $(MAX_REPO_KB) KB)"; \
	test $${tree:-0} -le $(MAX_REPO_KB) && test $${hist} -le $(MAX_REPO_KB) \
		|| { echo "Repo exceeds 9 MB"; exit 1; }

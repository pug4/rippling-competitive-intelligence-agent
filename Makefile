install:
	uv sync

format:
	uv run ruff format .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

test:
	uv run pytest -q

test-unit:
	uv run pytest tests/unit -q

test-contract:
	uv run pytest tests/contract -q

test-behavior:
	uv run pytest tests/behavior -q

eval-smoke:
	uv run competitive-agent eval --suite contracts

eval-all:
	uv run competitive-agent eval --suite all

demo-fixture:
	uv run competitive-agent demo-check --mode fixture

demo-cached:
	uv run competitive-agent demo-check --mode cached

quality: format lint typecheck test eval-smoke demo-fixture

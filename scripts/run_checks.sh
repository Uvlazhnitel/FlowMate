#!/bin/sh
set -eu

uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest tests/unit
uv run pytest tests/integration

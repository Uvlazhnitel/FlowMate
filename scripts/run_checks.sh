#!/bin/sh
set -eu

uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest tests/unit
uv run pytest tests/integration
npm run format:check --prefix apps/web
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm test --prefix apps/web
npm run build --prefix apps/web

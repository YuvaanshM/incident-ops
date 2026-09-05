SHELL := /bin/sh
PNPM := corepack pnpm
UV := uv

.PHONY: bootstrap dev-db dev-api dev-web test lint typecheck check seed down

bootstrap:
	$(UV) sync --project apps/api --all-groups --locked
	$(PNPM) install --frozen-lockfile

dev-db:
	docker compose up -d db

dev-api:
	$(UV) run --project apps/api uvicorn incident_ops.main:create_app --factory --reload --host 127.0.0.1 --port 8000

dev-web:
	$(PNPM) --filter @incident-ops/web dev

test:
	$(UV) run --project apps/api pytest apps/api/tests/unit apps/api/tests/api
	$(PNPM) test

lint:
	$(UV) run --project apps/api ruff check apps/api
	$(UV) run --project apps/api ruff format --check apps/api
	$(PNPM) lint
	$(PNPM) format:check

typecheck:
	$(UV) run --project apps/api mypy apps/api/src
	$(PNPM) typecheck

check: lint typecheck test

seed:
	$(UV) run --project apps/api python -m incident_ops.seed

down:
	docker compose down

SHELL := /bin/sh
PNPM := corepack pnpm
UV := uv

.PHONY: bootstrap dev-db test-db dev-api dev-web migrate migration-check test test-integration lint typecheck check seed down

bootstrap:
	$(UV) sync --project apps/api --all-groups --locked
	$(PNPM) install --frozen-lockfile

dev-db:
	docker compose up -d --wait db

test-db: dev-db
	docker compose exec -T db sh -s < scripts/ensure-test-db.sh

dev-api:
	$(UV) run --project apps/api uvicorn incident_ops.main:create_app --factory --reload --host 127.0.0.1 --port 8000

dev-web:
	$(PNPM) --filter @incident-ops/web dev

migrate:
	$(UV) run --project apps/api alembic -c apps/api/alembic.ini upgrade head

migration-check:
	$(UV) run --project apps/api alembic -c apps/api/alembic.ini check

test:
	$(UV) run --project apps/api pytest apps/api/tests/unit apps/api/tests/api
	$(PNPM) test

test-integration: test-db
	$(UV) run --project apps/api pytest apps/api/tests/integration

lint:
	$(UV) run --project apps/api ruff check apps/api
	$(UV) run --project apps/api ruff format --check apps/api
	$(PNPM) lint
	$(PNPM) format:check

typecheck:
	$(UV) run --project apps/api mypy apps/api/src
	$(PNPM) typecheck

check: lint typecheck test test-integration

seed:
	$(UV) run --project apps/api python -m incident_ops.seed

down:
	docker compose down

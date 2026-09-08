# Incident Ops

Incident Ops is a backend-first incident-response control plane where human
operators and narrowly scoped agents work from the same authoritative data. The
backend decides whether a requested action is auto-applied, held for human
approval, or rejected, and every meaningful change is auditable.

Phases 1 through 3 contain the reproducible repository, PostgreSQL/pgvector
foundation, and the complete FastAPI human/product API. Incident commands,
action policy, approvals, audit history, idempotency, concurrency control, and
the narrow demo retry executor work against PostgreSQL. The human web product,
production authentication provider, external delivery, retrieval calls, and
agent adapters remain in later phases.

## V1 boundaries

The planned v1 includes the incident lifecycle, immutable audit events,
optimistic locking, idempotent commands, approval workflows, one demo retry
executor, a transactional Slack outbox, and cited historical-incident retrieval.
It excludes production infrastructure remediation, Jira/Kubernetes automation,
multi-tenancy, autonomous closure/escalation, generic command execution, and
exactly-once delivery.

## Architecture

```text
Browser -> Next.js adapter -> FastAPI command/query services -> PostgreSQL + pgvector
                                 |                         |
                                 |                         +-> immutable audit/outbox
                                 +-> policy/auth boundary

Database outbox worker -> Slack (only after the command transaction commits)
```

Next.js and the later WebMCP integration are adapters. They never own business
rules or act as security boundaries. FastAPI application/domain services own
authorization, validation, policy, state transitions, idempotency, concurrency,
and audit behavior. PostgreSQL is authoritative.

See [ADR-0001](docs/adr/0001-monorepo-and-runtimes.md),
[ADR-0002](docs/adr/0002-backend-layering.md), and
[the security model](docs/security-model.md). The complete Phase 2 schema and
transaction rationale is in [the database foundation](docs/database.md).

## Prerequisites

- Python 3.14 (the newest patch release; `uv` installs it automatically)
- [uv](https://docs.astral.sh/uv/)
- Node.js 24 LTS (`nvm use` reads `.nvmrc`)
- Corepack with pnpm 11.25.0
- Docker Desktop or another Docker Compose v2-compatible runtime

Do not use npm/yarn or pip/Poetry for this repository. The committed uv and pnpm
lockfiles are the reproducibility boundary.

## Setup

```bash
cp .env.example .env
uv python install
corepack enable
corepack install
make bootstrap
make dev-db
make test-db
make migrate
```

Run the applications in separate terminals:

```bash
make dev-api
make dev-web
```

Then open `http://localhost:3000` and verify the API at
`http://localhost:8000/health/live`. Interactive API docs are available at
`http://localhost:8000/docs` outside production.

For the local Swagger walkthrough, enable `DEVELOPMENT_AUTH_ENABLED=true` only
in the ignored `.env`, then use the public fixture credential `human-demo` or
`agent-demo` in Swagger's Authorize dialog. See the
[Phase 3 API walkthrough](docs/phase-3-api.md).

## Commands

| Command | Purpose |
|---|---|
| `make bootstrap` | Install locked Python and Node dependencies |
| `make dev-db` | Start pgvector/PostgreSQL on loopback port 5432 |
| `make test-db` | Create the disposable sibling `incident_ops_test` database if needed |
| `make dev-api` | Start FastAPI on loopback port 8000 |
| `make dev-web` | Start Next.js on port 3000 |
| `make migrate` | Upgrade the configured database to the latest Alembic revision |
| `make migration-check` | Detect ORM/schema migration drift |
| `make export-openapi` | Deterministically export FastAPI's committed OpenAPI document |
| `make generate-api-client` | Regenerate the transport-only TypeScript API client |
| `make api-client-check` | Fail when OpenAPI or generated client artifacts drift |
| `make lint` | Run Ruff, ESLint, and formatting checks |
| `make typecheck` | Run mypy and TypeScript checks |
| `make test` | Run backend and frontend unit/API tests |
| `make test-integration` | Run real PostgreSQL migration, constraint, RLS, concurrency, and seed tests |
| `make check` | Run the complete local delivery gate, including PostgreSQL integration |
| `make seed` | Add deterministic synthetic Phase 2 fixtures |
| `make down` | Stop local Compose services without deleting the volume |

During development, run only the smallest relevant test file. The final
PostgreSQL gate is:

```bash
make test-integration
```

## Configuration

Process environment values override the optional root `.env` and
`apps/api/.env` files. `.env.example` contains harmless local placeholders only;
real credentials belong in the deployment platform's secret manager.

`APP_ENV` is `development`, `test`, or `production`. Production refuses local
HTTP public URLs, development authentication, and fixture reset flags. Fixture
loads are additive by default. A reset must be invoked explicitly with
`--reset`, `APP_ENV=development`, and `ALLOW_DEVELOPMENT_RESET=true`; all other
environments refuse it.

The embedding dimension is deliberately absent from environment configuration:
the migration fixes it at `vector(1536)`. Each historical record stores its
embedding provider and exact model, and retrieval must filter to that pair so
incompatible vector spaces are never mixed. Google `gemini-embedding-2` can be
configured for 1,536 output dimensions; Gemini Flash is a separate optional
answer-generation model. Free-tier external providers are permitted only for
synthetic demo records.

## Test layers

- Unit tests cover configuration, domain/policy rules, and application ports.
- API tests cover transport contracts and security headers.
- Integration tests use a real ephemeral PostgreSQL service.
- Vitest/Testing Library cover the web adapter; Playwright covers the demo path.

CI runs the same lint, type, unit, secret-scan, migration, constraint, RLS,
concurrency, pgvector, and database integration checks.

## Safety boundary

No browser or agent can approve actions, choose execution mode, provide actor
identity, or bypass backend policy. Future database access uses bound parameters
and deny-by-default RLS. Human sessions and agent tokens stay distinct; agent
tokens never receive approval, close, critical escalation, token administration,
or generic execution scopes. Full invariants are enforced as repository rules in
`AGENTS.md` and explained in `docs/security-model.md`.

The production CSP is nonce-based. Development alone relaxes script/style policy
for Next.js hot reload; that exception is explicit in `apps/web/proxy.ts` and is
not emitted in production.

## Demo flow

Phase 3 implements the non-UI control-plane flow: create/read incidents, request
a retry, auto-apply the first safe attempt, deterministically fail the demo job
again, hold the second attempt for a human, approve/reject it, inspect the
timeline, and surface stale optimistic-concurrency conflicts. Cited historical
retrieval and the browser experience arrive in later phases.

## Contributing

Use small topic branches and conventional commit prefixes (`feat:`, `fix:`,
`test:`, `docs:`, `chore:`). Run `make check` before pushing and follow
`CONTRIBUTING.md`. Never commit `.env`, tokens, database dumps, or webhook URLs.

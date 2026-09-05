# Incident Ops

Incident Ops is a backend-first incident-response control plane where human
operators and narrowly scoped agents work from the same authoritative data. The
backend decides whether a requested action is auto-applied, held for human
approval, or rejected, and every meaningful change is auditable.

Phase 1 contains the reproducible repository, API/web application shells,
PostgreSQL service, security baseline, and quality gates. It intentionally does
not yet contain incident tables, authentication flows, product screens, AI, or
WebMCP behavior.

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
[the security model](docs/security-model.md).

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
```

Run the applications in separate terminals:

```bash
make dev-api
make dev-web
```

Then open `http://localhost:3000` and verify the API at
`http://localhost:8000/health/live`. Interactive API docs are available at
`http://localhost:8000/docs` outside production.

## Commands

| Command | Purpose |
|---|---|
| `make bootstrap` | Install locked Python and Node dependencies |
| `make dev-db` | Start pgvector/PostgreSQL on loopback port 5432 |
| `make dev-api` | Start FastAPI on loopback port 8000 |
| `make dev-web` | Start Next.js on port 3000 |
| `make lint` | Run Ruff, ESLint, and formatting checks |
| `make typecheck` | Run mypy and TypeScript checks |
| `make test` | Run backend and frontend unit/API tests |
| `make check` | Run the local delivery gate |
| `make seed` | Add deterministic fixtures (none exist in Phase 1) |
| `make down` | Stop local Compose services without deleting the volume |

The PostgreSQL integration placeholder runs separately once the database is up:

```bash
uv run --project apps/api pytest apps/api/tests/integration
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

The embedding dimension is deliberately absent from environment configuration.
It determines a typed database column and remains the explicit D6 decision before
Phase 2 migrations.

## Test layers

- Unit tests cover pure configuration and, in later phases, domain/policy rules.
- API tests cover transport contracts and security headers.
- Integration tests use a real ephemeral PostgreSQL service.
- Vitest/Testing Library cover the web adapter; Playwright covers the demo path.

CI runs the same lint, type, unit, secret-scan, and database reachability checks.

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

The final demo will create a low-severity incident, retrieve cited precedent,
request a retry, show an auto-applied first attempt and human-gated second
attempt, and surface a deliberate optimistic-concurrency conflict. Phase 1 stops
at the secure application shell; this flow is implemented in later phases.

## Contributing

Use small topic branches and conventional commit prefixes (`feat:`, `fix:`,
`test:`, `docs:`, `chore:`). Run `make check` before pushing and follow
`CONTRIBUTING.md`. Never commit `.env`, tokens, database dumps, or webhook URLs.

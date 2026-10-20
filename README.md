# Incident Ops

Incident Ops is a backend-first incident-response control plane where human
operators and narrowly scoped agents work from the same authoritative data. The
backend decides whether a requested action is auto-applied, held for human
approval, or rejected, and every meaningful change is auditable.

The completed work (historical phases 1 through 5) contains the reproducible repository, PostgreSQL/pgvector
foundation, the FastAPI product API, and the responsive human console. Incident
commands, action policy, approvals, audit history, idempotency, concurrency
control, the narrow demo retry executor, explicit conflict recovery, and hosted
human authentication are implemented. The leased Slack outbox worker adds
bounded retries, crash recovery, and delivery auditing. Seven WebMCP tools use
short-lived agent credentials through server-side adapters. Phase 11A adds cited,
read-only retrieval from twelve synthetic historical incidents, with exact cosine
search and safe source links. Phase 11B adds optional bounded investigation with
quoted evidence, recoverable run receipts and policy-checked action requests.
It stays disabled pending its live synthetic evaluation and operator review.

The roadmap was reordered on 2026-09-17. Phase 8 now has two separate
creation paths: signed Slack issues-channel messages and signed generic
monitoring alerts. Both are disabled until explicit source bindings and secrets
are configured. See the [Slack intake runbook](docs/phase-8-slack-intake.md)
and [generic alert runbook](docs/phase-8-generic-alerts.md). A monitoring
recovery is evidence, not human incident resolution. Phase 9 now revalidates
remediation against current monitoring evidence, enforces action authorization
in application services, and provides explicit development job attachment. See
the [controlled remediation walkthrough](docs/phase-9-remediation.md).
The [WebMCP guide](docs/webmcp.md) covers Phase 10's trust boundaries, configuration,
native browser requirements and deterministic acceptance demo. The
[retrieval runbook](docs/phase-11a-retrieval.md) explains offline setup, the optional
OpenAI embedding adapter, provenance and model changes. The
[investigation runbook](docs/phase-11b-investigation.md) covers limits, cancellation,
recovery and the separate live model evaluation/setup gate. Existing phase-named runbooks retain
their historical filenames.

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

Next.js and WebMCP are adapters. They never own business
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

| Command                    | Purpose                                                                     |
| -------------------------- | --------------------------------------------------------------------------- |
| `make bootstrap`           | Install locked Python and Node dependencies                                 |
| `make dev-db`              | Start pgvector/PostgreSQL on loopback port 5432                             |
| `make test-db`             | Create the disposable sibling `incident_ops_test` database if needed        |
| `make dev-api`             | Start FastAPI on loopback port 8000                                         |
| `make dev-worker`         | Deliver queued Slack notifications (requires a configured webhook)          |
| `make dev-web`             | Start Next.js on port 3000                                                  |
| `make migrate`             | Upgrade the configured database to the latest Alembic revision              |
| `make migration-check`     | Detect ORM/schema migration drift                                           |
| `make export-openapi`      | Deterministically export FastAPI's committed OpenAPI document               |
| `make generate-api-client` | Regenerate the transport-only TypeScript API client                         |
| `make api-client-check`    | Fail when OpenAPI or generated client artifacts drift                       |
| `make lint`                | Run Ruff, ESLint, and formatting checks                                     |
| `make typecheck`           | Run mypy and TypeScript checks                                              |
| `make test`                | Run backend and frontend unit/API tests                                     |
| `make test-integration`    | Run real PostgreSQL migration, constraint, RLS, concurrency, and seed tests |
| `make test-agent`          | Run deterministic WebMCP acceptance with real FastAPI/PostgreSQL and a fake browser capability |
| `make check`               | Run the complete local delivery gate, including PostgreSQL integration      |
| `make seed`                | Add deterministic synthetic Phase 2 fixtures                                |
| `make down`                | Stop local Compose services without deleting the volume                     |

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

The production human console uses Auth0's authorization-code/session flow. Set
the blank `AUTH0_*` values for the Next.js encrypted HttpOnly session and the
matching `OIDC_*` issuer, audience, JWKS URL, and namespaced human actor claim
for FastAPI verification. The browser never receives the API access token; the
same-origin Next.js transport retrieves it server-side. See
[`docs/phase-4-ux.md`](docs/phase-4-ux.md) for the trust and interaction model.

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

## Slack notifications

Phase 5 delivers creation, severity escalation, and human-required action alerts
from the committed outbox. See the [worker runbook](docs/phase-5-outbox.md) for
configuration, local/Compose startup, retries, readiness, and dead letters. A
blank webhook leaves delivery disabled. Notifications use safe summaries and
review links; only a bounded, redacted monitor title can leave the app, never
descriptions or raw provider payloads. Delivery is at least once,
so a crash after Slack accepts a message may cause a duplicate carrying the same
stable event IDs.

## Demo flow

The Phase 4 browser flow creates and investigates an incident, requests a retry,
holds higher-risk work for approval, records approve/reject decisions, inspects
the audit ledger, and preserves a stale edit until its author reviews and
deliberately resubmits it. The Phase 8 monitoring flow can instead start with
the signed synthetic fixture: FIRING creates an incident, a higher source tier
asks for human review, RESOLVED records recovery evidence, and a human
resolves/closes the investigation. Cited historical retrieval arrives in
Phase 11A under the revised roadmap.

## Contributing

Use small topic branches and conventional commit prefixes (`feat:`, `fix:`,
`test:`, `docs:`, `chore:`). Run `make check` before pushing and follow
`CONTRIBUTING.md`. Never commit `.env`, tokens, database dumps, or webhook URLs.

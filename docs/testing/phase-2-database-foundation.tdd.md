# Phase 2 TDD evidence

Date: 2026-09-05

Scope: database-first backend foundation, pure incident transitions and action
policy, persistence/auth ports, PostgreSQL adapters, migrations, RLS, health and
error contracts, deterministic seeds, idempotency concurrency, and pgvector
retrieval support.

## RED checkpoints

- `77e7d9d test: define phase 2 domain and policy guarantees`
  - Exhaustive transition, retry-policy, allowlist, and principal-boundary tests
    failed during collection because the new domain/auth modules did not exist.
- `f8ae782 test: define phase 2 persistence guarantees`
  - Focused persistence/API tests failed during collection with missing
    `application.ports`, `api.dependencies`, and `infrastructure.database`
    modules. The integration collection separately failed on the absent database
    adapter, establishing an implementation-independent RED state.

## GREEN checkpoints

- `9d8c2e5 feat: add incident domain and policy foundation`
  - All 32 focused pure domain and authorization tests passed.
- `616f402 feat: add incident domain and database foundation`
  - Added two reversible Alembic migrations, seven RLS-protected tables,
    immutable events, explicit repositories/unit of work, auth/error adapters,
    database readiness, deterministic seeds, and the complete focused database
    suite.
- `6059d75 fix: require scopes for principal-owned database rows`
  - The completion audit tightened requester-owned action/idempotency policies
    so identity matching never substitutes for an applicable scope; the focused
    migration/RLS regression suite passed before the final delivery gate.

## Focused verification

- Unit/API: 55 passed.
- PostgreSQL integration: 27 passed, including base/head reversal, Alembic drift
  check, every named database constraint, restrictive foreign keys, immutable
  events, forced RLS, cross-principal denial, two-connection idempotency
  contention, explicit commit/rollback, deterministic seeds, exact cosine
  retrieval, provider/model filtering, and absence of an ANN index.
- Coverage checkpoint: 82 backend tests passed with 93.09% statement coverage
  (`--cov-fail-under=80`).

## Delivery gate

`make check` completed successfully after a formatting-only seed fixture issue
was corrected. The successful run included Ruff lint/format, mypy, ESLint,
Prettier, Python and TypeScript type checks, 55 backend unit/API tests, one web
unit test, and all 27 PostgreSQL integration tests.

# Phase 3 TDD evidence

Date: 2026-09-08

Scope: versioned incident/action HTTP API, strict validation, query/command
services, optimistic concurrency, command idempotency, rule-based advisories,
policy-driven demo retries, human approvals/rejections, development simulator,
RLS adjustment, deterministic OpenAPI, and the generated TypeScript transport
client.

## RED checkpoints

- `59229d8 test: define phase 3 incident API guarantees`
  - The focused unit tests failed because the idempotency helper did not exist;
    the PostgreSQL API tests failed because the application factory did not yet
    accept the database dependency.
- `74fdfc2 test: define policy-driven action guarantees`
  - The first action API contract received `404` because the action route and
    service had not been implemented.

## GREEN checkpoints

- `dbf83be feat: add incident command and query API`
  - Added incident create/list/detail/update/timeline endpoints, deterministic
    advisories, strict schemas, request correlation, rate limiting, atomic
    idempotency/event/outbox writes, and true two-writer conflict handling.
- `a72aab0 feat: add policy-driven action approvals`
  - Added action request/detail/queue/review endpoints, the narrow retry
    executor, development simulator, human-only approval, row-lock
    revalidation, persisted stale/race outcomes, RLS read policy, and a safe
    local Swagger identity adapter.

## Focused verification

- Incident foundation/contract tests: 11 passed.
- Incident PostgreSQL API tests: 7 passed.
- Action/policy/auth tests: 29 passed.
- Incident/action/RLS PostgreSQL tests: 19 passed.
- Migration upgrade/downgrade and RLS tests: 6 passed.
- OpenAPI convention tests: 8 passed.
- Generated TypeScript client lint and typecheck passed.

## Delivery gate

The final coverage result and `make check` outcome are recorded here after the
single end-of-phase full gate runs.

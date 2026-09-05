# ADR-0002: Ports-and-adapters backend layering

- Status: Accepted
- Date: 2026-09-03

## Context

The human UI and future agent adapter must call identical policy and persistence
logic. Framework-bound business rules would duplicate behavior and turn an
adapter into a security boundary.

## Decision

Organize the backend into domain, application, infrastructure, API, and worker
layers as those layers arrive. Domain code is pure and imports no framework or
provider SDK. Application command/query services enforce authorization, policy,
idempotency, and transaction boundaries through ports. Infrastructure implements
database, auth, Slack, and embedding ports. FastAPI and the worker are adapters
over those services.

## Consequences

Both human and agent requests receive the same checks. Domain behavior remains
fast to test. Some interfaces and mapping code are explicit, which is accepted
to keep transaction ownership and security boundaries reviewable.

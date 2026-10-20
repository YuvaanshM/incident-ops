# Phase 2 database foundation

Phase 11B adds owner-scoped `investigation_runs` with forced RLS. See
[optional investigation](phase-11b-investigation.md) for receipts, idempotency,
interrupted recovery and the separate action transaction.

PostgreSQL is the authoritative source for current state, immutable audit facts,
command idempotency, and notifications waiting for delivery. Domain code remains
independent of SQLAlchemy; application ports describe intentions and the
infrastructure layer implements them inside an explicit unit of work.

## Core tables

| Table | Purpose and important invariants |
|---|---|
| `incidents` | Current incident state. UUIDs and timestamps are database-generated; checked text constrains severity/status; lifecycle timestamps must agree with status; `version >= 1` supports conditional optimistic updates. Deterministic `(updated_at DESC, id DESC)` indexes support cursors. |
| `action_requests` | A proposal and its policy decision, not the audit log. It records the verified requester, observed incident version, canonical SHA-256 fingerprint, versioned policy snapshot, decision mode/status, and outcome. Principal-scoped idempotency is unique and pending work has a partial index. |
| `incident_events` | Append-only facts linked to an incident and optionally an action. A database trigger rejects both updates and deletes. Timeline ordering is deterministic on `(occurred_at DESC, id DESC)`. |
| `demo_jobs` | App-owned retry target with checked status, optimistic version, and a retry count constrained to `0..2`: one eligible automatic retry, one human-approved retry, then rejection. |
| `historical_incidents` | Versioned synthetic resolved precedents, provenance/content hash, narrative/resolution/steps, `vector(1536)` and exact provider/model/template metadata. Legacy records default to unreviewed and are excluded. |
| `historical_ingestion_events` | Append-only historical source versions, hashes, provider/model/template metadata and ingestion key, written atomically with history and the idempotency receipt. Forced RLS and an immutable trigger protect the audit. |
| `outbox_events` | Notifications committed with their source audit event. A unique stable dedupe key, availability time, attempt counter, processing lease, and terminal delivery/dead-letter states support concurrent `SKIP LOCKED` workers and crash recovery. |
| `idempotency_records` | Replayable command results keyed by verified principal, operation, and key. A reused key with another fingerprint is rejected; response/resource metadata is completed in the command transaction. |

All audit-bearing foreign keys use `ON DELETE RESTRICT`, checked domain values
are text rather than PostgreSQL enums, and no application repository exposes a
delete operation.

## Concurrency and transaction model

The default isolation level remains PostgreSQL `READ COMMITTED`. Incident writes
use a conditional `WHERE id = ... AND version = ...` update. Approval code
locks a still-pending action with `FOR UPDATE` and revalidates it in the same
transaction. Idempotency uniqueness serializes concurrent duplicate commands.
Outbox workers will claim independent rows with `FOR UPDATE SKIP LOCKED`; a live
lease prevents double-claiming and an expired lease is reclaimable.

A command unit of work is explicit. State changes, events, idempotency results,
and outbox rows share its commit or rollback. Network calls never occur inside
that transaction.

## Row-level security

Every domain table has RLS enabled and forced. Policies deny access when trusted
transaction-local settings are absent. The API sets `app.actor_type`,
`app.actor_id`, and JSON `app.scopes` only after token/session verification.
Scopes gate shared incident data; requester-owned action and idempotency data are
also principal-filtered. There is no tenant column because v1 is explicitly
single-tenant. Runtime roles must be non-owner, non-superuser, and `NOBYPASSRLS`;
deployment grants are intentionally outside schema migrations.

## Exact historical retrieval

The small corpus uses exact cosine search over `vector(1536)` with optional
service/severity filters and UUID tie-breaking. Provider/model/template checks
refuse incompatible seeded rows; no HNSW or IVFFlat index is introduced. Phase
11A implements OpenAI `text-embedding-3-small` and a deterministic local demo
adapter. Other embedding providers and answer generation are deferred. See
[ADR-0005](adr/0005-portable-rag-embeddings.md) and the
[retrieval runbook](phase-11a-retrieval.md).

Historical ingestion uses one server-owned transaction advisory lock and checks
the captured corpus versions before conditional writes. Its RLS writer is only
`system:historical-fixture-loader` with `retrieval:write`. Readers are authorized
through application `incidents:read` before receiving internal retrieval scope.
There is no public history mutation endpoint and no historical reset/delete
command; provider changes re-embed the complete small corpus atomically.

## Migration and test safety

Alembic first enables pgvector and then creates the versioned schema. Integration
tests refuse any target database whose name does not end in `_test`, exercise a
full base/head reversal, inspect every named constraint and restrictive foreign
key, and use real connections for RLS and concurrency. Seed loads are additive
with stable UUIDs. Reset deletes only fixture-owned IDs and requires both
`APP_ENV=development` and `ALLOW_DEVELOPMENT_RESET=true`.

# Phase 2 database foundation

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
| `historical_incidents` | Synthetic resolved precedents for later RAG. It stores the incident narrative, resolution, steps, `vector(1536)`, and the embedding provider/model that defines the vector space. |
| `outbox_events` | Notifications committed with their source audit event. A unique stable dedupe key, availability time, attempt counter, processing lease, and terminal delivery/dead-letter states support concurrent `SKIP LOCKED` workers and crash recovery. |
| `idempotency_records` | Replayable command results keyed by verified principal, operation, and key. A reused key with another fingerprint is rejected; response/resource metadata is completed in the command transaction. |

All audit-bearing foreign keys use `ON DELETE RESTRICT`, checked domain values
are text rather than PostgreSQL enums, and no application repository exposes a
delete operation.

## Concurrency and transaction model

The default isolation level remains PostgreSQL `READ COMMITTED`. Incident writes
use a conditional `WHERE id = ... AND version = ...` update. Approval code will
lock a still-pending action with `FOR UPDATE` and revalidate it in the same
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

## RAG support and Gemini

RAG is supported by the `historical_incidents` content fields, source IDs,
filter indexes, pgvector extension, and exact cosine distance over
`embedding vector(1536)`. Queries must filter by `embedding_provider` and
`embedding_model`; equal dimensions do not make different model spaces
compatible. The small v1 corpus deliberately has no HNSW or IVFFlat index.

Google `gemini-embedding-2` may be configured to produce 1,536-dimensional
vectors, while OpenAI `text-embedding-3-small` uses the same stored dimension.
Gemini Flash can separately generate an advisory answer from the retrieved,
cited rows; it does not generate the vectors and therefore does not change this
schema. Free-tier providers may receive synthetic fixtures only.

## Migration and test safety

Alembic first enables pgvector and then creates the versioned schema. Integration
tests refuse any target database whose name does not end in `_test`, exercise a
full base/head reversal, inspect every named constraint and restrictive foreign
key, and use real connections for RLS and concurrency. Seed loads are additive
with stable UUIDs. Reset deletes only fixture-owned IDs and requires both
`APP_ENV=development` and `ALLOW_DEVELOPMENT_RESET=true`.

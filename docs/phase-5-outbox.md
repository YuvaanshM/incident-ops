# Phase 5: Slack delivery

## Delivery contract

The API commits each incident/action mutation, audit event, idempotency result,
and notification fact in one transaction. It never contacts Slack. The worker
claims up to five due facts with `FOR UPDATE SKIP LOCKED`, commits their leases,
sends outside the transaction, then finalizes in a separate short transaction.

```text
pending (available_at reached) ─┐
                              ├─> processing (unique lease token)
processing (lease expired) ────┘      ├─> delivered + slack.delivered
                                     ├─> pending (retry scheduled)
                                     └─> dead_letter + slack.delivery_failed
```

Both terminal audit writes are atomic with the outbox status. They never enqueue
another notification. A finalizer must hold the current unexpired lease; an old
worker cannot overwrite a recovered attempt. Lease tokens change on every claim.
Database time controls eligibility, leases, and retries. Tests inject a clock.

A crash after Slack accepts a message but before the database commits delivery
can produce a duplicate. Delivery is **at least once**. Stable outbox and source
audit IDs appear in every copy; no exactly-once guarantee is made. A claim counts
as an attempt even if the process crashes. An exhausted recovered claim moves to
dead letter without another HTTP request (the stored counter caps at 25).

## Events and message safety

Supported facts are incident creation, every increase in severity, and an action
that requires human approval. Decreases and ordinary edits do not alert.
`slack.incident.critical` facts from Phase 3 remain supported alongside new
`slack.incident.escalated` facts. Old action facts without a severity snapshot
explicitly report “not recorded.” New facts capture severity at request time.

Messages contain server-authored summaries, severity, incident/review links, and
stable IDs. Free-form titles, descriptions, rationales, and errors are **not sent**:
arbitrary text can contain credentials that a pattern-based redactor cannot
reliably recognize. Links use the configured console URL and validated UUIDs.
Plain-text blocks and neutralized control characters prevent mention expansion
or user-controlled Slack markup. Review links open the human UI; there are no
approval buttons that execute actions from Slack.

Payload schemas reject unknown fields, malformed references, and unsupported
event types. These failures dead-letter without an HTTP request. Failure records
contain allowlisted codes; response bodies, webhook URLs, credentials, and
exception text never enter logs or audit payloads.

The worker uses a system principal with only `outbox:deliver`. RLS permits reading
outbox/source facts, updating delivery state, and appending matching delivery
audit records. It cannot read or mutate incident state. A small invoker-rights
SQL function checks source references under SELECT RLS without recursive policy
expansion; it grants no bypass. Command enqueueing uses INSERT without RETURNING
so human/agent commands do not need outbox read or delivery permissions. Use a
non-superuser, non-BYPASSRLS runtime database role in deployments. The local
Compose database owner is for development only.

## Failure handling and defaults

| Setting | Default | Bounds / behavior |
| --- | --- | --- |
| `WORKER_BATCH_SIZE` | 5 | 1–20, concurrent within one batch |
| `WORKER_POLL_SECONDS` | 2 | 1–60, including after failures |
| `WORKER_LEASE_SECONDS` | 60 | 30–600; at least total HTTP timeout + 15 |
| `WORKER_MAX_ATTEMPTS` | 5 | 1–25, includes interrupted claims |
| `WORKER_RETRY_BASE_SECONDS` | 5 | 1–300 |
| `WORKER_RETRY_MAX_SECONDS` | 300 | 1–3600; must be at least base |
| `SLACK_CONNECT_TIMEOUT_SECONDS` | 3 | 0.1–10 |
| `SLACK_READ_TIMEOUT_SECONDS` | 10 | 0.1–30 |
| `SLACK_TOTAL_TIMEOUT_SECONDS` | 15 | 1–45, covers entire HTTP attempt |

Write/pool timeouts are five/three seconds. Retryable results include network
failures, timeouts, HTTP 408/425/429/5xx, and malformed 200 responses. Success
requires HTTP 200 with `ok`. Other statuses, including redirects, are permanent
failures; redirects are never followed. The body read is capped at 64 bytes.
Delta-second `Retry-After` values are accepted with a configured upper bound;
malformed values are ignored. Exponential delay uses equal jitter (half to all
of the current exponential cap), with the bounded provider delay as a minimum.

HTTP behavior follows the [Slack incoming-webhook contract](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)
and [Slack text formatting rules](https://docs.slack.dev/messaging/formatting-message-text/).

## Running locally

1. Run `make dev-db`, `make migrate`, and the ordinary API/web processes.
2. Set `SLACK_WEBHOOK_URL` only in the ignored `.env` when ready to deliver to a
   selected Slack channel. Production accepts only HTTPS Slack webhook hosts.
   Development/test also accept explicit loopback mock URLs (or
   `host.docker.internal` for a host mock reached from Docker).
3. Check readiness with
   `uv run --project apps/api python -m incident_ops.worker.main --check`.
4. Start delivery with `make dev-worker`.

A blank webhook fails worker startup before any row is claimed. Starting the
worker drains existing pending notifications as well as new ones.

The optional Compose `notifications` profile contains API and worker processes:

```bash
make migrate
docker compose --profile notifications up -d --build api worker
docker compose logs worker
```

Stop host `make dev-api` first if using Compose's API on port 8000. Migration is
an explicit operator step; the worker does not run migrations. The images use
non-root processes, a read-only filesystem, and a temporary `/tmp` mount. SIGTERM
stops new claims and drains the current bounded batch. Compose allows 60 seconds
for shutdown. If interrupted during a transaction, its lease is recoverable.
The `--check` readiness command verifies configuration and database table access;
it does not contact Slack or mutate rows.

## Operations

Logs are JSON with event ID, source ID, originating request ID, attempt, result,
and whether the current lease finalized. HTTP/SQL library logging is suppressed.
Use the incident timeline for terminal delivery outcomes. Retry progress remains
in `outbox_events` (`attempt_count`, `available_at`, `last_error`).

For `dead_letter`, inspect the allowlisted failure code and stable IDs, repair
the webhook/configuration or malformed source-producing code, and preserve the
original audit record. V1 has no automatic or manual replay command. Do not
silently reset a dead-letter row: operator replay needs a separate explicit,
auditable command. Monitor backlog age and dead-letter counts in deployment.

## Verification

```bash
make check
uv run --project apps/api pytest apps/api/tests/worker -q
uv run --project apps/api pytest apps/api/tests/integration/test_outbox.py -q
pnpm test:e2e
pnpm build
```

The suite covers real PostgreSQL concurrent workers and locked-row skipping,
commit-before-HTTP, `500 -> 429 -> 200`, permanent failures, network timeout,
malformed responses, lease expiry, finite crash attempts, stale completion,
atomic audit rollback, no notification loops, and RLS denial for other actor
types and missing scopes. The webhook tests use local transports and loopback
servers; they never post to a Slack workspace.

### Development verification (2026-09-15)

- `make check`: passed (116 unit/API/worker tests, 55 PostgreSQL integration
  tests, 23 frontend component tests; lint, types, and generated-client drift).
- Playwright: all 8 human-console journeys/accessibility checks passed.
- Production Next.js build and API/worker image builds: passed.
- Container process smoke test against the disposable test database and a
  loopback webhook: `500 -> 429 -> 200`, stable message IDs, delivery audit,
  readiness, sanitized logs, and graceful SIGTERM passed.
- Gitleaks v8.30.1: no leaks in tracked and new repository files.
- UI inspected with synthetic data at 1440, 1100, 820, and 390px; no horizontal
  overflow. Real Slack workspace delivery remains an operator configuration step.

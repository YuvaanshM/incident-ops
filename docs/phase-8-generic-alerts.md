# Generic monitoring alert intake

Phase 8 adds one provider-neutral, synchronous webhook at
`POST /api/v1/alerts/webhook`. It accepts a normalized alert, not a native
Datadog/Prometheus/PagerDuty payload. The endpoint is disabled by default and
does not accept browser sessions or human/agent bearer tokens.

## Local setup

Configure a source in the ignored `.env` or a secret manager. The following is
the shape of `ALERT_INTAKE__SOURCES` (JSON); replace the example secret with a
unique printable 32–256-character value and never commit the live value:

```json
[{"source":"monitoring-demo-prod","services":["checkout"],"keys":[{"key_id":"synthetic-key","signing_secret":"replace-with-a-unique-local-secret-at-least-32-characters"}]}]
```

Set `ALERT_INTAKE__ENABLED=true` and restart the API. The sender needs the same
secret in `ALERT_SIGNING_SECRET`; load it from your secret manager or enter it
interactively into your shell without placing it on a command line. The
optional `ALERT_KEY_ID` defaults to `synthetic-key`. Multiple enabled keys on
one source support a bounded rotation overlap without changing alert identity.

Run `make test-db`, `make migrate`, `make dev-api`, `make dev-worker`, and
`make dev-web` as appropriate. With the API listening on loopback:

```bash
python scripts/send_demo_alert.py firing
python scripts/send_demo_alert.py update
python scripts/send_demo_alert.py resolved
```

These synthetic fixtures share source, logical alert ID, service, and occurrence
start. Each invocation gets a fresh idempotency key. To retry an event, pass
its original `--idempotency-key` UUID and the same fixture/occurrence; the
sender generates a fresh signing timestamp. To demonstrate a later occurrence
after an operator resolves the first incident, send `firing` with a later
`--started-at` RFC 3339 value (no more than five minutes in the future).
The checked-in fixture timestamps are historical by design. Run against a
clean local database for the first-time creation walkthrough.

## Delivery contract

Required headers are `Content-Type: application/json`, `X-Alert-Key-Id`,
`X-Alert-Timestamp` (Unix seconds within ±300 seconds), `Idempotency-Key`
(canonical UUID), and `X-Alert-Signature`. The signature is lowercase hex
HMAC-SHA256 prefixed by `v1=` over the original body bytes appended to:

```text
v1\nPOST\n/api/v1/alerts/webhook\n{key_id}\n{timestamp}\n{idempotency_key}\n
```

The signed source must match the envelope `source`; `service` must be in its
server-configured allowlist. Only the version-1 normalized schema is accepted.
One request contains one event. Body size is capped at 64 KiB; compression,
duplicate JSON keys, unknown fields, naïve/future timestamps, and unbounded
metadata are rejected. Outside loopback development, use HTTPS. An HMAC proves
which configured source sent the message, not whether the outage report is true.

Delivery identity is `(source, Idempotency-Key)`; logical alert identity is
`(source, external_alert_id, service)`; occurrence is `started_at`, ordered by
`event_version`. A retry must keep its key and normalized content. Reusing a
key with different content fails with 422. A different key for unchanged state
records a receipt but does not bump incident version or notify Slack again.
Older observations cannot clear newer firing occurrences. Equal-version,
different-state observations fail with 409. First firing creates an open
incident; recovery records source evidence but does **not** resolve or close
the human investigation. A later firing after the linked incident is terminal
creates a new incident. A newly higher reported severity asks for human review
without overwriting the authoritative incident severity or auto-running a
remediation.

`201` means an incident was created; `200` means an update, recovery, unchanged,
stale, suppressed, or orphan receipt was committed. `Idempotent-Replayed: true`
marks an exact replay. `429` and `503` are retryable with the original body/key
and a new timestamp/signature; fix other 4xx responses before retrying.
The response says `notification: queued`, never that Slack delivery succeeded.
Slack remains at least once and can duplicate after an ambiguous send.

Authorized incident readers use `GET /api/v1/incidents/{id}/alerts` to see
cursor-paginated normalized source history. Incident `monitoring_status` is
separate from human `status`. Raw provider payloads are never stored or
returned. Only allowlisted short operational labels from `metadata` are kept;
request/state hashes and immutable receipts preserve audit correlation.
Before enabling a real provider carrying sensitive fields, set a retention
period and redaction policy for its source-specific adapter. No native vendor
webhook or Slack thread synchronization is implied by this generic contract.

# ADR-0006: Slack issues-channel intake before generic monitoring

Status: accepted, 2026-09-17 (operator clarification during Phase 8 implementation).

The completed baseline covers the original phases 1–5, now phases 1–7. The operator
wants issues to arrive directly from a Slack channel. Implement a bounded Slack
Events API adapter as the first Phase 8 milestone. The generic alert occurrence,
recovery, correlation, and monitoring UI contract remains future Phase 8 work.

## Source and authority

Use `POST /api/v1/integrations/slack/events`. Authenticate the original bytes with
Slack's app signing secret, HMAC-SHA256 over `v0:{timestamp}:{body}`, a constant-time
comparison, and a 300-second clock window. This follows
[Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/).
An IP address, Origin, Referer, legacy verification token, retry header, or a human
session never authenticates this route. Knowledge of the signing secret conveys
signing authority; protect and rotate it independently of the outbound webhook.

After verification, require configured app, workspace, and channel IDs. Exclude
externally shared channels and mismatched source/context teams. Only ordinary
top-level human text messages are eligible. Bots, app messages, subtypes (including
edits/deletions/file shares), hidden messages, and thread replies are ignored.
Do not fetch attachments/URLs or execute instructions in message text. One new
post means one issue; this milestone does not classify arbitrary conversation.

The application derives a `system` principal `slack:<team>:<channel>` with exactly
`slack:ingest`. Server configuration supplies service and initial severity, default
medium. Payload authority fields are discarded. This source can only create an
open incident; it cannot mutate an existing incident, submit actions, approve,
resolve, close, or deliver outbox messages. Application checks remain mandatory
even when a database owner connection bypasses RLS.

## Persistence and retries

Reuse the creation primitive inside one existing unit of work. Create the incident,
immutable `incident.created` event with normalized source IDs, notification intent,
and completed idempotency records in one PostgreSQL transaction. No network call
occurs inside it. No new tables are needed for immutable message-to-incident intake.
The additive migration grants source-isolated RLS on existing tables and no new
incident UPDATE/action authority; trusted transaction settings bind service/severity.

Two reservations share a canonical normalized fingerprint: `slack.delivery` uses
app/team/event ID, and `slack.message` uses team/channel/message timestamp. Both
are namespaced by verified source principal. This covers transport retries and
different event IDs describing the same message. Changed content with reused
identity fails with the existing 422 idempotency mismatch. Signing-secret rotation
does not change identity. Ignored events need no durable receipt because they have
no effects. Stored responses contain only `{ "ok": true }`, not incident data.

The signature window alone does not prevent a same-window replay; durable unique
reservations do. We never delete receipt records as part of routine processing.
No original envelope, legacy token, signature, headers, or original byte body is
stored. Message text remains untrusted incident content; do not post credentials
or private material to this issues channel. The notification worker retains its
existing safe message format and omits source text/descriptions from Slack delivery.

## Availability and deliberate limits

[Slack expects acknowledgment within three seconds](https://docs.slack.dev/apis/events-api/).
This bounded MVP does only a small local transaction before acknowledgment, with a
two-second application deadline, one-second lock timeout, and 1.5-second statement
timeout. Failures return a generic retryable 503; success follows commit. Ambiguous
commit/response failure is safe to retry. A durable inbox/worker is a future scaling
step if measured latency or traffic requires it, not an in-memory background task
that acknowledges before durable storage.

Limit bodies to 64 KiB while streaming, messages to 5,000 characters, provider JSON
depth to 12, objects to 100 keys, and arrays to 1,000 items. Reject duplicate keys,
non-finite numbers, invalid UTF-8/surrogates, NULs, compression, and ambiguous
security headers. Discard unknown provider metadata for Slack forward compatibility;
the internal command is a fixed validated projection. Rate limits are 120 attempts
per minute globally before verification and 30 accepted-message attempts per
minute per configured channel. Limits are per API process; edge limits are required
before scaling to multiple processes. Unknown input cannot grow limiter key state.

The endpoint is disabled by default, requires HTTPS outside direct loopback
development, accepts no browser cookies/bearer credentials, and is excluded from
the human/agent generated API client. This document and the runbook define the
provider transport. History backfill, bot-originated monitoring, Slack Connect,
recovery semantics, and reply synchronization each require a later explicit scope.

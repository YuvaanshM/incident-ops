# ADR-0003: Command idempotency and retry policy

Status: Accepted

Date: 2026-09-05

## Context

Every mutation must be safe to repeat after a timeout or lost response. Action
requests already carry an idempotency key, but incident mutations and approvals
need the same protection. The demo also needs to show both a narrow automatic
remediation and a later human approval without allowing an unbounded retry loop.

## Decision

- Require an `Idempotency-Key` UUID on every mutating HTTP endpoint once those
  endpoints are introduced.
- Store a general idempotency record keyed by principal type, principal subject,
  operation, and idempotency key. Store a SHA-256 fingerprint of canonical
  request data and the completed response.
- Keep the action request's principal-scoped idempotency key as domain
  traceability in addition to the general response record.
- Treat an identical replay as the original command. Reject reuse of the same
  key with a different fingerprint as
  `idempotency_key_reused_with_different_payload`.
- Allow one automatic `retry_failed_job` for an eligible low-severity incident.
  A second retry requires human approval. Reject any third retry. API replays
  and outbox delivery attempts do not increment the demo job's retry count.
- Keep `request_status_update` as a recognized, always-human-reviewed proposal.
  Reject unknown action types instead of silently treating them as pending.

## Consequences

Concurrent requests serialize on database uniqueness and replay one stored
response. A policy change to the retry cap requires an explicit migration and
policy-version change. The demo can exercise automatic, pending, approved, and
rejected outcomes without permitting a retry storm.

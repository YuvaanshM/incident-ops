# ADR-0004: Transactional outbox claiming

Status: Accepted

Date: 2026-09-05

## Context

Slack delivery must happen after the authoritative command commits. Multiple
workers must be able to deliver concurrently, and a worker crash must not leave
an event permanently stuck in `processing`. Dedupe by incident alone would
incorrectly suppress later legitimate notifications.

## Decision

- Insert an outbox row in the same transaction as its source audit event and
  authoritative state change.
- Link the outbox row to its immutable source event and give it a unique stable
  dedupe key derived from that event and notification type.
- Claim eligible rows with `FOR UPDATE SKIP LOCKED`.
- Record `locked_by` and `lease_expires_at`; an expired processing lease is
  reclaimable.
- Track availability, attempts, sanitized last error, delivery time, and the
  terminal `dead_letter` state.
- Perform provider network calls only after the claiming transaction commits.

## Consequences

Workers can scale horizontally without double-claiming live leases. Delivery
remains explicitly at least once: a crash after Slack accepts a message but
before the delivered update commits may produce a visible duplicate.

# Phase 8 generic alert foundation: TDD evidence

## Source and scope

The guarantees in this slice come from Phase 8A and 8B of the local
`IMPLEMENTATION_PLAN.md` and the generic alert webhook contract in
`PROJECT_SPEC.md`. Those planning files remain local source material. This slice
implements source-bound HMAC verification, typed key rotation configuration, and
strict normalized alert parsing. It does not expose the HTTP route or persist an
alert yet.

## User journeys

- As a configured monitoring source, I can sign one generic alert delivery and
  receive only the source-bound `system` identity and `alerts:ingest` capability.
- As an operator rotating a source key, I can overlap two configured keys without
  changing the durable source identity or idempotency namespace.
- As the incident service, I receive one bounded, immutable, UTC-normalized alert
  value, while ambiguous JSON and invalid lifecycle data are rejected before
  application policy runs.

## RED and GREEN report

| Task | RED evidence | GREEN evidence | Guarantee |
|---|---|---|---|
| Alert signing boundary | Focused pytest collection failed because `infrastructure.auth.alert_webhook` and `monitoring_config` did not exist. | `test_alert_signature.py`: 19 passed. | Exact v1 HMAC bytes, five-minute delivery window, raw-body integrity, UUID delivery key, disabled/unknown key denial, stable rotation identity, narrow scopes, and secret redaction. |
| Generic normalization | Focused pytest collection failed because `domain.alerts` did not exist. | `domain/test_alerts.py`: 28 passed. | Strict v1 fields, `ACTIVE` normalization, UTC timestamps, lifecycle ordering, immutable metadata, size/range limits, duplicate-key rejection, and unsafe JSON rejection. |
| Adjacent contracts | Not applicable; existing tests remained GREEN after the new types were wired in. | `test_config.py` and `test_persistence_contracts.py`: 9 passed. | Existing settings safety and provider-neutral application ports remain valid. |

Focused GREEN command:

```text
UV_CACHE_DIR="$PWD/.cache/uv" uv run --project apps/api pytest \
  apps/api/tests/unit/test_alert_signature.py \
  apps/api/tests/unit/domain/test_alerts.py \
  apps/api/tests/unit/test_config.py \
  apps/api/tests/unit/test_persistence_contracts.py
```

Result: 56 passed.

## Coverage and known gaps

The focused coverage command passed at 88.10% across the five new alert modules
(47 tests). The HTTP router, database tables/RLS, alert lifecycle service,
transactional audit/outbox behavior, human read model, and concurrency tests are
deliberate follow-up work in the same Phase 8 branch. No external network or live
monitoring provider was used.

## Checkpoints

- RED: `411e565 test: define generic alert trust boundary`
- GREEN: the next implementation commit on `codex/phase-8-alert-ingestion`

# Phase 4 TDD evidence

Date: 2026-09-10

Scope: responsive human incident console, create/edit/retry flows, immutable
timeline presentation, approval queue/detail, explicit stale-write recovery,
approval-race recovery, same-origin mutation transport, hosted Auth0 sessions,
provider-neutral OIDC verification, and approval incident/age filters.

## RED checkpoints

- `261b3f0 test: define phase 4 human console journeys`
  - Incident list, form, detail, and timeline component contracts failed before
    the human console existed.
- `a992c39 test: define approval and conflict journeys`
  - Approval modules were missing and stale-write responses had only a generic
    error path.
- The OIDC unit target failed because no hosted token verifier existed; the
  server-transport target returned `503` instead of using the hosted session.
- Approval filter tests showed that incident and age values never reached the
  API. The focused PostgreSQL test then proved both filters at the real query
  boundary.
- The first Axe browser scan found invalid ARIA table children and two contrast
  failures. Those findings were kept as regression coverage and corrected.

## GREEN checkpoints

- `08e3ba5 feat: add human incident console`
  - Added the incident ledger, create/detail/edit, demo job retry request,
    timeline, narrow same-origin routes, and all initial/empty/error states.
- `25cddb6 feat: add approval and conflict UX`
  - Added the approval desk, readable decision context, approve/reject controls,
    visible polling, neutral race recovery, and deliberate stale-draft review
    and resubmission.
- Hosted authentication now uses the official Auth0 Next.js SDK with encrypted
  HttpOnly sessions and a disabled client token endpoint. FastAPI independently
  verifies RS256 signature, issuer, audience, time claims, subject, actor type,
  and scopes/permissions.
- Focused component, transport, OIDC, PostgreSQL filter, browser journey,
  keyboard/narrow-screen, and Axe targets pass.

## Test boundaries

Playwright fakes the owned BFF response boundary to keep browser journeys
deterministic and offline. It does not claim database or authorization safety.
Those guarantees are exercised in FastAPI/PostgreSQL tests, including the
existing coordinated stale-writer and simultaneous-approval races.

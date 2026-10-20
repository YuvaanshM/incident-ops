# Phase 11B test evidence

Tests preceded their corresponding production behavior. Initial unit/provider/
evaluation tests failed on absent modules; web tests failed on absent exports
and the UI component. The first browser attempt returned the expected disabled
feature error before the browser-only scripted model was injected.

Observed regression red/green cycles:

- Status-action integration exposed an incorrect command identifier; the adapter
  now dispatches the existing `request_status_update` command.
- Cancelling an ASGI request left its receipt running because Starlette's AnyIO
  scope interrupted cleanup. Shielded finalization now records cancellation;
  replay makes no new provider call.
- Historical citation links initially omitted the observed source version; they
  now preserve that provenance.
- Explicit schema assertions now include the new table, constraints and forced RLS.
- An unexpected provider failure initially lost the reserved-cost accounting;
  the terminal error now keeps the ceiling and excludes private error details.
- Native browser acceptance exposed Chrome 155's object-input `executeTool`
  interface. The test driver now matches the verified current specification;
  registered product callbacks already accepted objects.

Tests cover fabricated citations, denied tools/actions, unknown fields, actor and
scope boundaries, expiry, outage, budgets, repeated calls, injection through both
alert/history text, stale versions, concurrent replay/admission, rollback, critical
approval, cancellation, interrupted recovery and RLS read/write denial. Database
races use a provider barrier, not sleeps. The browser journey gathers evidence,
produces quotes and leaves critical remediation pending for a human.

Model-quality evaluation is separate. Its scoring is unit-tested, but the live
result requires provider credentials and a reviewed synthetic report.

## Handoff verification: 2026-10-20

Revalidated the combined working tree before committing: `make check` passed
387 backend, 90 frontend and 135 real PostgreSQL tests, including migration
upgrade/downgrade/drift and forced-RLS checks. The production build passed;
Auth0's existing dynamic-dependency warning remains. Human browser regression
passed 10 journeys. Agent/investigation acceptance passed 5 journeys with native
Chrome enabled; 3 duplicate native-inapplicable cases were skipped. Gitleaks
found no secrets in the non-ignored source files or existing Git history.

The optional investigation remains disabled. `INVESTIGATION_API_KEY` is absent
and the live evaluation gate remains incomplete; deterministic results do not
establish live model quality. No external provider call was made.

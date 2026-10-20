# Phase 9: controlled remediation after monitoring intake

An alert creates an investigation, not an executable infrastructure target.
Remediation is still an explicit human/agent request to FastAPI. The only executor
queues an app-owned failed demo job; no external production job, shell, cloud, or
Kubernetes operation is available.

## Policy and review

Policy `2026-10-08.v3` uses persisted incident severity/status, the failed job's
ownership/version/retry count, and linked firing alerts. The request stores these
facts and the policy decision. A higher firing severity records the explicit
`higher_monitoring_risk` reason without changing human severity.

| Current facts | Result |
|---|---|
| Active, low-severity incident; first retry; no higher firing risk | Applied automatically |
| Higher firing risk, higher stored severity, or second retry | Pending human approval |
| Third retry | Rejected; `retry_limit_reached` |
| Resolved/closed investigation | Retry rejected; `incident_not_active` |
| Status-change proposal | Always requires human approval |
| Unknown action, foreign/missing/nonfailed job, invalid parameters | Refused |

An approval locks the pending action, then its incident and job. It rechecks the
incident version, job state/version, action registry and current policy, including
monitoring risk. A policy-version change also makes old proposals stale. The
original request snapshot remains intact; `action.approved` records the revalidated
policy, incident version and job version in the immutable timeline. A human can
approve a freshly requested higher-risk retry after review; higher risk blocks
*automatic* execution, not authorized human decisions.

Meaningful source changes (including recovery) increment the incident version.
An earlier proposal stays pending until reviewed, then approval returns `stale`
without executing it. A heartbeat with unchanged meaningful content does not
invalidate a proposal. Recovery alone never approves an action or resolves the
human investigation. Rejection can cancel a pending proposal regardless of whether
its evidence changed, because it executes nothing.

Both ingestion and approval serialize on the incident row. If ingestion commits
first, approval observes the new version and becomes stale. If approval holds the
incident lock first, it finishes against the last committed evidence before the
alert can update that incident. Approval reads alert risk without locking alert
rows, avoiding an inverted lock order with ingestion. PostgreSQL tests observe
actual blocked transactions in both orders.

Application services enforce actor type and scopes before opening a transaction,
including when called without HTTP. Monitoring principals cannot use any action
query, request, review, or fixture command, even if mistakenly assigned operator
scopes. Transaction-local internal scopes permit authorized row locks and executor
writes; they never become permissions in a human/agent token. A non-bypass RLS
integration scenario exercises the complete workflow with a narrowly scoped agent
and a human reviewer.

## Explicit development job attachment

`POST /api/v1/demo/incidents/{incident_id}/job` accepts only:

```json
{"expected_incident_version": 1}
```

It requires a verified human with `fixtures:write` and a UUID `Idempotency-Key`.
The server supplies every job field. The incident must be open/investigating and
at the expected version. A new fixture returns `201`, increments incident version,
and commits the job, `demo_job.attached` audit event and idempotency result together.
Refresh the incident before requesting remediation.

The fixed fixture identity permits only one such job per incident. Repeating the
original key returns the original response. Using a new key at the current version
returns the existing job (`200`), preserving its status and counters. Attachment
cannot reset or bypass the retry cap. Concurrent attachments are serialized.
Stale input returns `409`; changed input with a used key returns `422`.

This route and the existing job-failure simulator are absent in production.
The application service also refuses both when demo access is disabled. No new
secret, database table, migration, or production fixture permission is needed.

## Local human walkthrough

1. Follow [generic alert setup](phase-8-generic-alerts.md) for a synthetic source.
   Enable development authentication only in the ignored local `.env`. Start the
   database, apply migrations and run `make dev-api`. The optional Slack worker
   uses the existing outbound setup; external delivery is not needed for this demo.
2. Send `python scripts/send_demo_alert.py firing`. Record the returned incident
   ID and version. Verify the incident has no job until explicitly attached.
3. Open the local API docs at `http://localhost:8000/docs`, authorize with the
   public development credential `human-demo`, and call the attachment route with
   that incident ID/version and a fresh UUID idempotency key. Inspect the incident
   again; a new attachment makes version 1 become 2.
4. Call `POST /api/v1/incidents/{id}/actions` with another UUID key, the current
   incident version and the attached job ID:

   ```json
   {
     "expected_incident_version": 2,
     "action_type": "retry_failed_job",
     "parameters": {"job_id": "<attached job UUID>"},
     "rationale": "Retry the synthetic failed job after reviewing source evidence."
   }
   ```

   The checked-in firing fixture starts at high severity, so the result is pending.
   If Auth0 is configured for the web console, the same request and review are
   available on the incident and approval pages. Swagger uses the same services
   when hosted browser authentication is not configured.
5. Before approving, send `python scripts/send_demo_alert.py update`. Approve the
   old request with a fresh UUID key. It becomes stale and the job remains failed.
   Fetch the latest incident, submit a new request at its version, and approve
   that request. It becomes applied, with the job queued and retry count 1.
6. Call `POST /api/v1/demo/jobs/{job_id}/simulate-failure` as the human with a new
   UUID key. Request another retry at the current incident version. It needs human
   approval; approve it. Simulate failure once more and request a third retry:
   policy rejects it and the count stays 2. To demonstrate an automatic first
   retry separately, use the low-severity checkout fixture from `make seed` and
   [the original API walkthrough](phase-3-api.md).
7. Send `python scripts/send_demo_alert.py resolved`. The source reports recovery;
   the incident remains open/investigating. Update it to resolved and then closed
   as the human, using a fresh idempotency key and the latest version each time.
   Inspect the immutable source, decision and execution timeline.

Use the same occurrence for firing/update/recovery. The checked-in fixtures already
share it. Repeated complete demos need a separate synthetic alert identity or a
fresh disposable development database; do not reset real incident data.

## Verification

Run `make check` (includes PostgreSQL migration/RLS/concurrency tests), then:

```bash
corepack pnpm --filter @incident-ops/web test:e2e --workers=1
```

Serial browser execution avoids development-server navigation aborts seen during
parallel cold page compilation. The browser suite checks pending/applied/rejected
states, approval conflicts, stale recovery, monitoring visibility, accessibility,
and responsive layouts. Browser tests use transport fixtures; database integration
tests use the real application services and PostgreSQL. Neither configures Auth0
nor contacts a live Slack/monitoring provider.

Secret scanning uses Gitleaks' default rules plus two narrowly scoped allowances
in `.gitleaks.toml`: three exact, public synthetic values in the Phase 8 signature
test file, and the literal documentation fragment `disabled/unknown` in its
historical test report. All seven pre-existing findings were those false positives.
The allowances require both the path and exact detected value; no rule, directory,
or commit is excluded. Two synthetic canaries verified that a different value in
that test file and an allowed value outside that file still trigger detection.
See the [Gitleaks configuration reference](https://github.com/gitleaks/gitleaks/blob/v8.30.1/README.md#configuration)
for the inherited-rule and AND-condition behavior.

### Verification record (2026-10-08)

- `make check`: passed; generated contract drift, formatting, lint/security rules,
  Python/TypeScript types, 241 unit/API/worker tests, 24 frontend component tests,
  and 110 PostgreSQL integration tests (including migration and RLS checks).
- Human-only and narrow-agent API walkthroughs passed using a database role
  without RLS bypass. Both include signed intake, fixture attachment, retry policy,
  human approval, retry-cap rejection, source recovery and human resolution/closure.
- Playwright: all 10 browser journeys passed with `--workers=1`; the initial
  parallel run had three development-server navigation aborts.
- `corepack pnpm build`: passed.
- Gitleaks v8.30.1: working files/local planning documents and all 27 existing
  commits passed with the exact synthetic/prose allowances described above.
- No live external workspace, provider delivery, or hosted-auth login was exercised.

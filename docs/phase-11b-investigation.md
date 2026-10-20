# Optional bounded investigation

Phase 11B adds an on-demand **Investigate with AI** panel to incident detail.
It uses the existing server-side agent exchange, then a FastAPI-owned loop over
the existing incident, timeline, normalized alert, retrieval and action services.
The seven WebMCP registrations are unchanged. Manual workflows, monitoring,
approvals and deterministic severity recommendation require no model.

The feature is **disabled by default**. Deterministic tests do not establish
live model quality; the synthetic model evaluation is a separate enablement gate.

## Behavior and limits

A run is bound to one incident and version. It reads the incident and can read
one bounded page each of timeline, normalized alerts and historical precedent.
The summary contains up to five exact quotes checked against observed sources.
Historical citations include their observed version. Quotes establish
attribution, not truth or causality. React escapes the output; no generated HTML
or links are trusted. Raw webhook payloads, actor IDs and credentials are excluded.

Actions are off by default. Explicit checkbox opt-in permits one retry or status
request under existing policy. Incident ID, version, actor, scopes and request
key remain server-owned. A retry job must have been observed on this incident.
The action service still locks, authorizes and revalidates all policy facts.
Critical remediation remains pending human approval. There is no approval,
incident close, critical escalation, token administration or generic execution tool.

The exact model is OpenAI `gpt-5.4-mini-2026-03-17`, stateless Responses JSON
with no hosted tools or retries. Work has a 40-second deadline, six-step ceiling,
8 KB context ceiling, 1,000 output tokens per response, 60,000 reserved tokens
and USD 0.10 reserved cost ceiling. Reads cannot repeat. Each call reserves its
worst-case input/output before dispatch; failed calls keep their reservation.
Reported usage and reserved cost are different measures, not an invoice.
Receipt cleanup and HTTP transport may finish after model work stops.
See [ADR 0007](adr/0007-bounded-investigation.md) for prompt, pricing and privacy.

## Durability and recovery

`POST /api/v1/incidents/{id}/investigations` requires a verified delegated agent,
both `incidents:read` and `actions:request`, a UUID `Idempotency-Key`, and:

```json
{"expected_incident_version": 2, "allow_action_requests": false}
```

One transaction records the run, start audit and idempotency receipt. Only one
unexpired run per principal is admitted. No transaction stays open around model
calls. Finalization checks the run version and atomically records its outcome
and redacted audit. Forced RLS restricts run rows to their delegated owner.

Same-key replay returns the existing run without restarting work; a changed body
is rejected. `GET /api/v1/investigations/by-request/{key}` recovers an owned run
even when the provider is disabled. **Check investigation** uses a fresh agent
credential. The original token is never refreshed during execution; expiry stops
the loop. Copy the recovery key to use after navigating away.

**Stop investigation** aborts the request and records cancellation when possible.
It cannot undo an action already committing. The receipt includes a fixed action
request key: use **Check action request** before starting again after uncertainty.
Process loss or final-transaction failure can leave a running receipt. After its
deadline recovery labels it interrupted; it never automatically resumes.

Advisory runs create no notification outbox rows. Requested actions use the
existing atomic action/audit/idempotency/outbox path. Audit traces include only
tool/status/source IDs, model/prompt metadata and usage counts. Selected quotes
are stored in the owner-scoped result, not the audit trace. No prompts, provider
bodies, secrets or reasoning traces are logged.

## Setup after coding

1. Run `make migrate` and restart API/web. Complete Phase 10 delegated-agent and
   Phase 11A retrieval setup. Confirm the deployment has **only synthetic data**.
   Deterministic embeddings can remain in use; generation is a separate port.
2. Put `INVESTIGATION_API_KEY` in ignored `apps/api/.env` or a secret manager.
   Never paste it in chat or use a `NEXT_PUBLIC_` value. Leave investigation disabled.
3. Run the explicit live synthetic evaluation:

   ```sh
   uv run --project apps/api python -m incident_ops.evaluate_investigation \
     --live --repeats 3 --output /tmp/incident-ops-investigation-eval.json
   ```

   It sends only embedded fixtures, accesses no database, and performs no actual
   action. Four scenarios cover cited extraction, alert injection, history
   injection and an opt-in retry proposal. Three repeats reserve at most USD 1.20.
   Every attempt must pass completion, observation coverage, citation fidelity,
   relevance and appropriate-action checks. Missing credentials, provider failure,
   refusal, incomplete response or a budget stop do not pass.
4. Manually review every saved quote and action choice, especially injection
   influence. Record the report location, model, prompt version and date. Only
   after automatic checks and review pass, set `INVESTIGATION_EVALUATION_PASSED`,
   `INVESTIGATION_SYNTHETIC_DATA_CONFIRMED` and `INVESTIGATION_ENABLED` to `true`.
5. Restart the API. Walk a read-only run and an opted-in critical retry through
   the UI. Inspect `investigation.started`, `investigation.completed` and the
   agent `action.requested` with delegation metadata. The critical request must
   remain pending until a human approves. Keep the receipt/policy result with
   the reviewed evaluation. Re-evaluate after prompt/model/pricing changes.

Production enablement is rejected in this phase. Real data requires a separate
reviewed processing and rollout decision. `store:false` is not a zero-retention
guarantee; the text scrubber is not a general PII detector.

## Verification status

Deterministic tests cover fabricated sources/quotes, forbidden tools, missing
opt-in, unobserved jobs, repeated calls, budgets, outage, timeout, cancellation,
expiry, stale versions, injection, strict schemas and provider response handling.
Real PostgreSQL tests cover concurrent replay/admission, atomic rollback, forced
RLS, critical approval, cancellation and interrupted recovery. The browser test
injects a scripted model only into its loopback fixture server; it proves wiring
and policy, not model quality. The live evaluation's scoring has separate tests.

The final local gate passed on 2026-10-17: `make check` (387 backend, 90 frontend
and 135 PostgreSQL tests), production build, 10 human browser journeys, and 5
agent/investigation browser journeys including Chrome 155. Three duplicate
native-inapplicable cases were intentionally skipped. Gitleaks found no secrets
in working source or 27 commits of history. Existing Auth0 build warnings remain.
See
[test evidence](tdd/phase-11b-investigation.tdd.md) for red/green observations.

Live evaluation on 2026-10-17: **not run**, because `INVESTIGATION_API_KEY` was
not configured. The command exited before provider calls. The full Phase 11B
enablement gate remains open until a live report passes and is reviewed.

# ADR 0007: Optional, extractive incident investigation

Status: accepted for implementation; external demo enablement requires the live synthetic evaluation gate.
Date: 2026-10-17.

The FastAPI application owns the investigation loop. It starts from a verified,
short-lived delegated agent credential and invokes the same application queries
and action commands as the existing seven-tool adapter. No eighth generic WebMCP
tool, independent agent permissions, execution runtime, learned severity model,
or background resumption is introduced. Next.js only forwards and renders.

## Provider and prompt

Use OpenAI Responses at the fixed `https://api.openai.com/v1/responses` endpoint,
snapshot `gpt-5.4-mini-2026-03-17`, reasoning effort `none`, strict JSON schema,
`store:false`, no hosted tools, previous-response state, redirects, retries, or
provider-side background execution. The prompt version is
`extractive-investigation-v1`, defined in the application contract. Provider
availability is not assumed. [Model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
and [structured-output contract](https://developers.openai.com/api/docs/guides/structured-outputs)
were checked on 2026-10-17.

The model chooses bounded observations and extracts one to five exact quotes.
The backend checks each quote against the cited source actually observed during
that run. This establishes attribution, not truth or causality. Current incident,
normalized alert and historical text remain untrusted quoted data. Output is
escaped by React; no generated HTML, links, free-form rationale, or reasoning is
executed or rendered as trusted markup. Retrieval still uses Phase 11A's separate
embedding port; severity remains the deterministic rule service.

## Limits and authority

One initial incident read, three optional read tools (timeline, normalized alerts,
precedent), and one terminal decision; no repeated read tool. Six-step ceiling,
40-second total deadline, 8,000-byte context, 1,000 output tokens per response,
60,000 reserved tokens and USD 0.10 reserved cost per run. Input reservation uses
UTF-8 bytes plus the exact prompt/schema and 1,024 bytes framing headroom. Cost
reserves USD 1/M input and USD 5/M output, conservatively above the documented
USD 0.75/M and USD 4.50/M; no caching discounts. Failed calls keep their full
reservation. Usage outside reservation fails closed. Review prices before any
enablement or model change. HTTP transport has an eight-second per-call deadline.

Every step re-verifies the original credential and incident version. At most one
allowlisted action may be requested, only with explicit start-time opt-in. Its
incident ID, version, actor and idempotency key are server-owned. The ordinary
action service applies policy, locks, approval requirements and atomic audit /
outbox writes. The harness cannot approve, close incidents, escalate severity,
manage credentials or issue generic commands. It stops after the action request.

## Durability and cancellation

PostgreSQL records the start receipt, run ownership, deadline, optimistic run
version and audit in one transaction, then the terminal outcome and audit in
another. Network calls hold no database transaction. A repeated start key never
restarts provider work; recovery reads the same run. Keys are scoped to the
delegated principal. Forced RLS restricts run records to that principal. A single
active run per principal is admitted under a database transaction lock.

Disconnect cancels the synchronous run. Cancellation and deadlines are terminal;
there is no automatic retry. A request already committing cannot be undone by
disconnecting. The fixed action-request key is returned in the start receipt and
can be recovered through the existing action recovery adapter. Process loss can
leave a running receipt: after its deadline recovery labels it interrupted and
never resumes it. A new run requires an explicit new key. Traces contain only
tool names, statuses, source IDs and usage/reservation counts, never prompts,
provider payloads, tokens or reasoning. Citation quotes are stored in the final
advisory result, not in the audit trace.

## Data handling and rollout

Disabled by default. The only initial external-data mode is explicitly confirmed
synthetic deployment data. No production environment enablement is supported in
this phase. Before real data, a separate reviewed processing arrangement must
specify retention, region, access and redaction; `store:false` alone is not a
zero-retention guarantee. Normalized evidence fields are allowlisted, bounded,
and scrubbed for markup, hidden controls and common credential/URL patterns;
this is defense in depth, not proof that arbitrary real text contains no PII.
No raw provider webhook payload or actor identifier is sent to the model.

Deterministic fake-provider authorization/adversarial tests and real PostgreSQL /
browser tests are separate from the live synthetic model-quality evaluation.
Run the explicit evaluation command with a separately configured secret. Keep
the demo disabled until its reviewed report passes relevance, evidence fidelity,
injection and allowed-policy scenarios. Manual workflows require no model.

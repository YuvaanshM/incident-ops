# WebMCP and the Incident Ops trust boundary

WebMCP lets a page describe a small set of functions for a compatible browser
agent. A tool wrapper validates a narrow input and translates it into an ordinary
API request. It does not decide who is authorized, whether a retry is safe, or
whether an action needs approval. Those decisions remain in FastAPI services.
The human console continues to work without this browser capability.

## Browser contract checked before implementation

On 2026-10-08, the [WebMCP draft](https://webmachinelearning.github.io/webmcp/)
defines `document.modelContext`. `registerTool(tool, {signal})` returns a promise;
aborting that registration signal removes the tool. The callback receives input
and `{signal}` for that invocation. Registration and invocation cancellation have
separate lifetimes. No legacy `navigator.modelContext` shim or fictional
registration handle is used.

The [Chrome imperative API guide](https://developer.chrome.com/docs/ai/webmcp/imperative-api)
documents this registration form and independent unregistration in Chrome 153+.
Target that experimental shape in a secure context (HTTPS, or trusted localhost).
Feature detection, not browser-name guessing, controls registration. Exact native
browser verification and limitations are recorded below after validation.

## One retry call, end to end

1. A browser agent calls `request_retry_failed_job` with an incident ID, expected
   incident version, app-owned job ID and a rationale. The wrapper rejects extra
   fields, creates one idempotency key and posts to a same-origin adapter.
2. Next.js obtains the verified human's access token server-side. FastAPI exchanges
   it for a five-minute agent credential with exactly `incidents:read` and
   `actions:request`. The human must hold both permissions. The caller cannot choose
   scopes, subject, actor type, lifetime, issuer or audience.
3. Next.js forwards the command using only the agent credential. It never falls
   back to the human credential for a tool call. Credentials stay in server memory;
   neither the browser response, JavaScript storage nor cookies hold an agent token.
4. FastAPI verifies the dedicated signature, issuer/audience, expiry, actor type
   and capped scopes. Application authorization and policy then recheck incident,
   monitoring and job facts. The incident version and idempotency key protect the
   request against stale evidence and duplicate delivery.
5. A first eligible low-risk retry may apply; otherwise it is pending or rejected.
   Changes, the request snapshot, audit, idempotency result and required outbox
   rows commit together. Approval remains a separate human action.

The token exchange is stateless authentication, not a persisted product command.
It stores no credential in an idempotency record or database. Each token has a
unique `jti`; its agent subject is stable for the same verified human, so refreshing
credentials cannot evade principal rate limits or break command replay. Delegated
command audit events include the verified human subject and token ID. There are no
new tables or RLS policies; existing principal-isolated idempotency storage applies.
Incident readers can inspect action status by action ID under the existing shared
incident read contract. Recovery by idempotency key is deliberately narrower: the
query also matches the verified requesting principal.

## Seven tools, fixed targets

| Tool | Backend operation |
|---|---|
| `search_incidents` | Incident search GET |
| `get_incident_timeline` | Incident timeline GET |
| `classify_incident_severity` | Deterministic severity advisory POST |
| `draft_postmortem` | Templated postmortem draft POST |
| `suggest_remediation` | Read-only cited historical precedent POST |
| `request_retry_failed_job` | Registered retry action request POST |
| `request_status_update` | Registered status proposal POST; always human-reviewed |

Schemas reject unknown fields and bound text, result limits, UUIDs and versions.
The status tool only proposes investigating/resolved, and the backend separately
refuses agent close proposals. No approval, rejection, closing, token administration,
execution, arbitrary URL, SQL or shell tool is exposed. Phase 11A now implements
`suggest_remediation`; see the [retrieval runbook](phase-11a-retrieval.md). An LLM
is not needed here. Traditional MCP
and a standalone MCP server are outside this phase.

## Threats and failure behavior

- **Spoofed identity or wider permissions:** reject caller-owned actor/scope fields.
  Agent JWT verification is separate from hosted human authentication, with an
  explicit algorithm and token type. An agent cannot mint another agent token.
- **Session riding:** all browser adapter operations require same-origin POSTs,
  an intent header and JSON. Hosted authentication keeps its Secure/HttpOnly/
  SameSite cookie protections. Client bearer headers are never forwarded.
  CSRF compares against the configured `APP_BASE_URL` origin; proxy forwarding
  headers cannot choose that origin.
- **Confused deputy:** tool names map to fixed backend paths; unknown tools and
  extra input fields are rejected. The adapter never chooses an execution mode.
- **Credential leakage:** signing material exists only in typed backend settings.
  The browser gets structured results, never an access token. Responses are
  non-cacheable; errors do not include upstream diagnostics or credentials.
- **Prompt injection:** incident titles, descriptions and timeline text are
  untrusted data. Results preserve source/resource IDs and declare untrusted
  content. Tool code never interprets record text as an instruction or executes it.
- **Cancellation or connection loss:** pass invocation cancellation through each
  fetch and add a bounded server timeout. Aborting stops waiting; it cannot undo
  a committed transaction. Return the generated idempotency key and a fixed
  recovery endpoint that looks up only this agent principal's request. A new
  credential keeps the same subject. A missing result is not proof of rollback;
  an in-flight request can still finish. Do not silently submit a new command.
- **Navigation or partial registration failure:** retain one AbortController per
  registration, remove all owned tools during cleanup, and leave the human app
  usable if the capability is absent or registration fails. Never clear tools
  belonging to another component.
- **Abuse:** token exchange has a separate human-principal limit. Agent command
  limits key on the stable verified principal, not rotating token IDs. Single-
  process rate limiting retains the documented v1 deployment limitation.

## Operator configuration

Agent tools remain disabled until `AGENT_TOKENS_ENABLED=true` and the backend's
`AGENT_TOKEN_ISSUER`, `AGENT_TOKEN_AUDIENCE` and `AGENT_TOKEN_SIGNING_KEY` are set.
Use separate agent and human issuers/audiences and a cryptographically random
signing key of at least 32 bytes in a secret manager. Next.js needs no signing key.
Production still requires Auth0. Explicit local development authentication can
exercise the same exchange with the existing public human fixture credential.

Set Next.js `APP_BASE_URL` to the exact public browser origin (including a local
port), and `API_INTERNAL_URL` to the backend. Put Next.js variables in its process
environment or ignored `apps/web/.env.local`; the API reads the root `.env`.
Do not copy the API signing key into Next.js configuration. Enable the backend
flag only after setting all three agent credential values, then restart it.
Use a different key per environment; disabling the flag rejects outstanding agent
tokens immediately. Rotating the key invalidates them too. Individual JWTs are
stateless and expire within five minutes; there is no token-administration UI.

For native browser tools, the tested setup is Google Chrome **154.0.8037.99** with
the experimental WebMCP feature enabled. The [Chrome WebMCP guide](https://developer.chrome.com/docs/ai/webmcp)
describes the testing flag and origin-trial option. HTTPS or trusted localhost is
required. Unsupported browsers retain the normal human console. This is an
experimental browser integration, not a promise that every browser agent supports it.

## Verification

Run the deterministic demo without a model or external credentials:

```bash
make test-db
corepack pnpm --filter @incident-ops/web exec playwright install chromium
make test-agent
# Optional installed Google Chrome, including visible native verification:
WEBMCP_NATIVE_CHROME=1 corepack pnpm --filter @incident-ops/web exec playwright test --config playwright.agent.config.ts --headed
```

The harness starts loopback FastAPI on 8108 and Next.js on 3108. It migrates only
the sibling database whose name ends in `_test`, generates synthetic monitor
occurrences, and leaves the development database alone. Do not run it concurrently
with integration tests that reset that disposable database. Test credentials are
public synthetic fixtures supplied by the harness, never production credentials.
The harness ingests the twelve synthetic historical sources with the deterministic
local embedder. No Slack worker or external model provider runs in this demo.

The driver signs an alert, attaches the development job, searches through an agent
tool, obtains cited historical precedent and opens a source page, obtains the
deterministic advisory/draft, applies the first eligible retry,
simulates another failure, and requests a second retry. It approves that pending
request through the ordinary human UI and checks system/agent/human audit actors,
delegation metadata and a visible stale-version conflict after new source evidence.

The Chrome 155 testing `executeTool` accepts a JavaScript input object and returns a
JSON-string result; the fake driver matches this current test interface. The
[current specification](https://github.com/webmachinelearning/webmcp/blob/main/index.bs)
documents this change from Chrome 154's string input. Product callbacks
receive parsed objects and invocation signals and do not depend on that testing
interface. Registration uses the current promise/AbortController API, including
navigation cleanup. The native journey and unsupported Chromium 151.0.7922.34
fallback were also run in visible windows. Browser cancellation can reject native
execution before returning a callback value; the page still displays the uncertain
mutation and its recovery control while mounted. A page reload loses that transient
control; use the audited action record if a command's response was lost.

Core token, transport and lifecycle tests were observed failing before implementation. Tests
cover fixed JWT claims/lifetime, delegation refusal, wrong actor/scope, unknown
fields, direct close/critical/approval denial, strict versions, rate limits that
survive token refresh, replay mismatch, own-request recovery, idempotency RLS,
untrusted record text, cancellation and registration failure. The complete delivery
gate passed locally on 2026-10-08:

- `make check`: generated-client drift, lint/format/types, 273 backend/unit/API/
  worker tests, 73 frontend tests and 114 real PostgreSQL tests. The PostgreSQL
  suite includes migration round trips/drift, RLS, transaction faults and races.
- Existing human Playwright journeys: 10 passed. Agent/fallback suite: 4 passed
  including native Chrome; 2 duplicate fake/fallback cases are intentionally
  inapplicable to the native project. The same suite passed in visible windows.
- Production build passed. Existing Auth0 dynamic-import build warnings remain;
  no dependency was added in this phase.
- Gitleaks passed for source files (including new files) and all 27 existing
  commits; credentials/logs remain redacted. Remote CI and hosted Auth0/Slack
  were not exercised by these local checks.

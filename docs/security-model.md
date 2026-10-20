# Security model

## Trust boundaries

The browser, WebMCP tools, request bodies/headers, retrieved incident text,
Slack, OIDC providers, and embedding providers are outside the core trust
boundary. FastAPI verifies identity and permissions and validates every value.
PostgreSQL is authoritative; UI state and tool annotations are never authority.

## Identity and authorization

Use a mainstream OIDC/session provider for humans; never implement password
cryptography. Sessions use Secure, HttpOnly, SameSite cookies with CSRF protection
on state changes. Agents use separate short-lived, audience-bound tokens with
least-privilege scopes. Agent credentials cannot approve/reject actions, close an
incident, critically escalate it, administer tokens, or execute generic commands.

Actor type and ID come only from a verified credential. Every protected FastAPI
route checks both scope and actor type. The browser cannot elevate permissions.

Phase 10 exchanges a verified human credential for a five-minute agent JWT with
exactly `incidents:read actions:request`. The dedicated issuer, audience and signing
key are server settings; callers cannot select claims. Next.js holds the credential
only in server memory and forwards a fixed tool route. Agent command audit events
carry the delegating human and credential ID, and stable subjects retain rate and
idempotency identity across token refresh. The stateless authentication exchange
does not persist tokens or domain mutations. See the [WebMCP threat model](webmcp.md)
for cancellation/recovery, browser compatibility and setup.

## Database and SQL

All SQL uses SQLAlchemy expressions or bound parameters. User values are never
concatenated into SQL; dynamic identifiers require a server-owned allowlist.
When Phase 2 creates tables, it must enable and force RLS where appropriate,
revoke broad public privileges, add explicit least-privilege policies, and add
tests proving cross-principal denial. Application authorization remains required
even for server connections capable of bypassing RLS.

Every mutation uses optimistic concurrency and an idempotency key. Mutable rows,
immutable audit events, idempotency results, and required outbox rows commit in
one transaction. Approval rows are locked and policy is revalidated during the
approval transaction.

## Input, output, and transport

Strict request schemas reject unknown command fields and enforce size/range
limits. Public errors use a stable generic envelope and never return traces,
queries, secrets, or provider payloads. Logs redact tokens, cookies, database
URLs, webhooks, signing keys, and sensitive headers.

React escapes text by default. User HTML is not rendered. Retrieved records are
untrusted historical context, include source IDs, and cannot supply instructions.
Slack output escapes/normalizes incident text and suppresses uncontrolled mention
expansion.

The web app emits a per-request nonce CSP in production plus HSTS, clickjacking,
MIME-sniffing, referrer, permissions, and opener protections. Next.js hot reload
requires a documented development-only CSP relaxation. CORS uses exact configured
origins, never a wildcard with credentials. The database binds only to loopback
in local Compose.

## Abuse and dependencies

APIs use principal-aware rate limits, with tighter budgets for agents and
expensive work. Forwarded IP headers are trusted only behind a configured proxy.
Lockfiles are committed, CI scans for leaked secrets, and dependency updates are
tracked automatically. Production secrets live in the deployment secret manager.

## Phase gates

The implementation plan was reordered on 2026-09-17. Historical phases 1-5
already supplied setup, persistence/RLS, the API, human UI/auth, and the Slack
worker. In the new learning sequence, auth is Phase 5, core workflow is Phase 6,
Slack is Phase 7, monitoring ingestion is Phase 8, controlled remediation is
Phase 9, WebMCP is Phase 10, and optional investigation is Phase 11. Existing
controls remain in place; this renumbering does not disable or postpone them.

## Slack channel intake (first Phase 8 milestone)

Slack intake is disabled by default. Verify Slack's HMAC over original body bytes
and reject delivery timestamps outside 300 seconds before JSON parsing. Require
the configured app/workspace/channel, exclude shared channels, and reject browser
credentials. Ignore bots/replies/edits to prevent loops. Strict bounded provider
normalization yields only text and source IDs; service/severity come from server
configuration. See [ADR-0006](adr/0006-slack-channel-intake.md).

The source-bound `system` principal has only `slack:ingest`. It can create an open
incident and its audit/outbox/idempotency records in one transaction; it cannot
update incidents or operate actions. Additive RLS policies enforce actor, scope,
source isolation, and configured creation values. Durable message and event
reservations prevent duplicate creation even within the signature replay window.
No raw Slack envelope, legacy token, signature, or headers enter persistence/logs.
The existing notification worker still omits message text from outbound payloads.

## Planned generic monitoring boundary (remaining Phase 8 work)

Monitoring sources use a separate signed, timestamp-checked webhook with only
source/service-bound `alerts:ingest` authority. A verifier derives the system
principal; neither payload fields nor a human/agent token can supply it. Check
strict body limits, source-scoped idempotency, provider occurrence/version, and
default-deny RLS on alerts and their immutable receipt history. Never grant
general incident-update authority to all system principals.

The ingestion service may initialize a new incident, including its reported
critical severity, through a shared transaction. It cannot escalate an existing
incident, overwrite a human's edits, approve/execute remediation, or resolve/close
an investigation from a monitor's recovery signal. Recovery and later severity
reports update source context and audit. Higher reported risk requests human
attention and prevents automatic remediation from using an outdated low label.

Alert, incident, audit, idempotency, and outbox writes commit atomically. External
monitors do not know incident versions; the backend reads and checks the version
inside the transaction, while source versions order monitoring observations.
Meaningful updates invalidate stale proposals; identical heartbeats do not.

The planned creation notification includes a bounded/redacted safe alert title,
service, source, severity, and server-built link. Keep plain-text rendering and
mention suppression; descriptions/raw provider payloads stay out of Slack.
Signatures, keys, original bodies, and sensitive headers never enter logs. Later
provider or Slack-channel adapters must use this same application boundary.

## Cited history (Phase 11A)

Retrieval is an authorized read for human/agent principals with `incidents:read`.
Only the application adds the internal `retrieval:read` database scope. Returned
source prose and steps are untrusted text with stable UUID/version citations;
there is no answer generation or automatic control flow from retrieved content.
The browser renders plain strings and fixed internal source links. Tight
principal-based query limits protect the embedding dependency, which is disabled
by default and requires explicit external-data configuration for OpenAI.

A separate fixed maintenance principal can publish synthetic history, with forced
RLS policies restricted to `system:historical-fixture-loader`. Version checks,
content fingerprints and an advisory transaction lock protect corpus changes.
History, append-only ingestion audit and the principal-scoped idempotency receipt
commit together; no notification is emitted. Provider calls run before writes
and outside the transaction. See [the retrieval runbook](phase-11a-retrieval.md)
for bounded inputs/outputs, model-space checks, failure behavior and setup.

## Optional investigation (Phase 11B)

The disabled-by-default harness uses a dedicated generation port and verified
delegated agent identity. Every step checks the original token and incident
version. Fixed tools, one-use observations, deadlines and reserved token/cost
ceilings constrain the loop. Cited findings must be exact substrings of evidence
actually observed. This proves attribution only; source text remains untrusted.

Action opt-in permits at most one request through the existing policy service;
no model output can supply actor/scopes or bypass human authority. Run receipt,
idempotency and start audit commit together; terminal outcome/audit use an
optimistic run version. Forced RLS restricts reads and internal writes to the
owner. No raw prompts, provider payloads, reasoning or credentials enter traces.
Cancellation does not undo a committed action; fixed keys enable recovery.
See [ADR 0007](adr/0007-bounded-investigation.md) and the
[setup/evaluation runbook](phase-11b-investigation.md). Production enablement
is rejected until a separate data-processing and rollout decision is implemented.

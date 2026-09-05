# Security model

## Trust boundaries

The browser, future WebMCP tools, request bodies/headers, retrieved incident text,
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

APIs will use principal-aware rate limits, with tighter budgets for agents and
expensive work. Forwarded IP headers are trusted only behind a configured proxy.
Lockfiles are committed, CI scans for leaked secrets, and dependency updates are
tracked automatically. Production secrets live in the deployment secret manager.

## Phase gates

Phase 1 establishes these rules and secure defaults but does not pretend to
implement later controls. Phase 2 must add parameterized persistence and tested
RLS. Phase 3 must add verified auth dependencies, scope/actor checks, strict API
schemas, idempotency, rate limiting, and authorization tests. Phase 4 must add the
chosen human OIDC flow and CSRF handling. Phase 7 may add only the narrow agent
tools in the specification.

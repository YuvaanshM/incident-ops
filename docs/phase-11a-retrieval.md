# Cited historical incident retrieval

Phase 11A adds advisory precedent from twelve curated, synthetic resolved incidents.
The console and `suggest_remediation` return source records and ordered resolution
steps. They do not synthesize answers or execute those steps. Current incident
policy, approvals, severity and lifecycle remain authoritative.

## Embeddings and provider selection

An embedding represents text as a numeric vector. Exact cosine similarity orders
nearby vectors; a high score means similar text, not a probability that a fix will
work. This remediation template includes severity and resolution context and must
not be reused for Phase 12B severity prediction.

ADR-0005 selects one external implementation: OpenAI `text-embedding-3-small`, with
1,536 dimensions, behind `EmbeddingPort`. The deterministic `hash-bow-v1` adapter
hashes words into the same number of dimensions for offline demos and tests. It
is labelled **Deterministic demo matching** and is not an evaluated semantic model.
Its vectors must never be compared with OpenAI vectors.

The [OpenAI embeddings reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
and [embedding guide](https://developers.openai.com/api/docs/guides/embeddings) were
checked on 2026-10-12. The adapter uses the fixed HTTPS embeddings endpoint,
`encoding_format=float`, the exact model, and explicit `dimensions=1536`. There is
no generation model, provider SDK, arbitrary endpoint, redirect following, retry
loop, or added dependency.

## Setup

For a no-key local demo, set these API environment values in the ignored root
`.env` or process environment, then restart the API:

```dotenv
EMBEDDING_PROVIDER=deterministic
EMBEDDING_MODEL=hash-bow-v1
```

From a source checkout at the repository root:

```bash
make migrate
uv run --project apps/api python -m incident_ops.seed historical
# Repeating the same command returns the recorded result without re-embedding.
```

Open an incident and select **Find historical precedent**. WebMCP also needs the
existing agent-token setup in [the WebMCP guide](webmcp.md). Retrieval itself does
not require WebMCP. The default provider remains `disabled`, which returns a
generic 503 for suggestions while source records remain readable.

For external semantic matching, choose `EMBEDDING_PROVIDER=openai` and
`EMBEDDING_MODEL=text-embedding-3-small`. Supply `EMBEDDING_API_KEY` privately to
the API/maintenance process, and set `EMBEDDING_ALLOW_EXTERNAL_PROCESSING=true`
only after approving the data-processing arrangement. These values are validated
at startup. Never place the key in Next.js, `NEXT_PUBLIC_*`, fixtures, or Git.
Run the same ingestion command to re-embed the complete small corpus, then restart
the API with the matching settings. During a model switch, incompatible retrieval
returns unavailable instead of mixing vectors. A separate checkout/maintenance
job is needed for migrations and seeding; the existing worker image does not
bundle the migration directory or source fixture directory.

No paid API call is needed for tests. This phase was validated with deterministic
providers and a protocol-accurate HTTP transport fake; live OpenAI credentials and
billing were not exercised. Network selection is an operator setup step, not a
claim of live provider acceptance.

## Cost, data, and failure behavior

An initial import makes one embedding request per source (twelve requests for the
bundled manifest); changed content or a provider/model/template switch re-embeds
affected rows. An unchanged import makes none. Each nonempty-corpus query makes
one request, including when filters or the similarity threshold yield no matches.
There is no embedding cache or automatic retry. OpenAI charges for embedding input
usage; check current pricing and set account usage alerts before enabling it.

The provider receives bounded title, description, service, severity and resolution
summary fields on ingestion. Query embedding uses the current incident's title,
service, severity and description, or the caller's optional query as description.
Source IDs, actors, tokens, audit events and resolution steps are not embedded.
The versioned `remediation-v1` template is canonical JSON. Field byte budgets are
600/3000/300/2000 respectively for title/description/service/summary; serialization
is further bounded to 8,000 UTF-8 bytes without breaking JSON or Unicode. This is
a conservative input bound, not a token estimate.

The curated manifest rejects markup, hidden controls, URLs and common credential
patterns. Query preparation removes markup/hidden controls and redacts common
credential patterns. These checks are not a general secret detector: use only
synthetic data for demos and review real-data processing before enabling external
calls. Instruction-like prose is always data, never an instruction to this app.

The HTTP adapter uses a five-second request timeout and six-second total deadline,
a 256 KiB response limit, no redirects and no automatic retries. It verifies the
reported model, one output at index zero, exactly 1,536 finite numeric values and
a nonzero norm, then normalizes before float32 storage. Cancellation propagates.
Errors are generic; credentials, input text and provider response details are not
logged. Responses are never cached. Queries are capped at twenty per minute per
human and ten per minute per agent; these are single-process v1 limits.

## Ingestion and database safety

`fixtures/historical-incidents/incidents.json` contains twelve stable UUIDs across
checkout, payments, database, notifications, search and identity, with all four
severities, resolved status and `seeded_demo_data` provenance. No real company or
customer records are included. Legacy rows default to `unreviewed` and are excluded
from retrieval until explicitly republished as reviewed synthetic sources.

Only the server maintenance principal `system:historical-fixture-loader` with
`retrieval:read` and `retrieval:write` can ingest. No ingestion HTTP route or agent
tool exists. Content hashes cover all validated source fields, including ordered
steps. A manifest/provider/model/template hash determines its default idempotency
key. Replaying an old manifest returns that command's original receipt; it does
not undo a later import. Controlled maintenance can supply a new explicit UUID
through the ingestion service to intentionally republish an older manifest.

Embeddings are obtained before opening the write transaction. A server-owned
advisory transaction lock serializes small-corpus writes; captured source versions
and the whole corpus snapshot are rechecked before conditional updates. Divergent
concurrent imports conflict, while identical imports replay. History rows, version
increments, append-only `historical_ingestion_events` and the idempotency receipt
commit atomically. There is no notification intent, so ingestion emits no outbox
row. Audit records contain identifiers, hashes and embedding metadata, not source
text or credentials. RLS is forced on history and audit tables, with explicit
maintenance-write policies and no audit update/delete policy; an immutable-event
trigger also protects the audit from owner updates.

The service accepts at most twenty distinct sources in this v1 corpus. Provider,
model or template changes require all existing seeded sources in the manifest.
There is no deletion/reset command. For deployment, grant the maintenance role
only necessary table access and keep database credentials server-side. The API's
internal retrieval scope is added only after verifying a human/agent incident
reader. Public agent tokens still have only their existing capped scopes.

## Query and presentation contract

`POST /api/v1/incidents/{id}/remediation-suggestions` accepts `{}` or bounded
`query`, `service`, `severity`, and `limit` fields. Unknown fields are rejected.
`limit` defaults to 3 and must be an integer from 3 through 5. Exact cosine search
filters the compatible seeded corpus and optional exact service/severity; scores
below 0.25 are omitted. Equal scores sort by source UUID. There is no ANN index.
An empty corpus returns `empty_corpus`; filters/low similarity return `no_matches`.
The incident version is rechecked after embedding; a changed incident returns 409.

Results include `advisory: true`, incident ID/version, matching mode, provenance,
and each source UUID/version, score, summary and ordered steps. The UI labels
synthetic provenance and scores, escapes all strings, and offers source links.
`GET /api/v1/historical-incidents/{id}` returns the source record without vectors;
a source page warns if its current version differs from the citation. New incident
versions clear prior UI results. Retrieval has no mutation controls and never
changes incidents, actions, audit events or approvals.

The seventh browser tool accepts only `{incident_id, query?}` and forwards a
read-only request using the server-issued agent credential. It is annotated as
read-only and untrusted content. It was registered only after endpoint auth and
citation tests passed. Browser cancellation uses the existing abort-signal path;
it cannot create an uncertain mutation because retrieval is read-only.

## Verification and scaling

Focused red/green tests cover vectors/provider contracts, strict source/query
schemas, deterministic ranking/ties/filters, empty/no-match behavior, same-key
replay and mismatch, stale versions, concurrent imports, rollback, forced RLS,
instruction-like content, source escaping, and absence of incident/action changes.
The browser demo loads synthetic history and exercises retrieval and a source link
before the existing agent retry → human approval → audit → stale-evidence journey.

```bash
uv run --project apps/api pytest apps/api/tests/unit/test_retrieval.py apps/api/tests/unit/test_embedding_provider.py apps/api/tests/api/test_remediation_suggestions.py
uv run --project apps/api pytest apps/api/tests/integration/test_pgvector_retrieval.py
make check
make test-agent
```

Before increasing corpus size, measure exact-search latency and relevance with a
reviewed evaluation set. Only then consider an ANN index and compare its recall
and filtered-query behavior to the exact baseline. Raising the twenty-source cap,
changing thresholds and selecting a new model require explicit evaluation and a
migration/ingestion plan. Phase 11B optional investigation and Phase 12B severity
assistance remain separate, unimplemented extensions.

The complete local gate passed on 2026-10-12: 329 backend tests, 81 frontend tests,
124 PostgreSQL tests, 10 human browser journeys, 4 agent/native/fallback journeys
(with 2 inapplicable native duplicates skipped), and the production build. See
[the test evidence](tdd/phase-11a-retrieval.tdd.md) for red/green details.

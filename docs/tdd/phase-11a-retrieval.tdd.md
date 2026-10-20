# Phase 11A retrieval: verification evidence

The phase boundary is cited historical retrieval, without model-generated answers
or investigation orchestration. Tests were added before the production surfaces.

| Contract | Observed failure before implementation | Passing evidence |
|---|---|---|
| Vector/source/query validation | New retrieval modules were absent; focused collection failed. | Dimension, nonfinite/zero/coercion, plain synthetic records, bounds and stable template tests. |
| External embedding adapter | Provider/error class absent; focused collection failed. | Fixed endpoint/model/dimensions, malformed response, timeout, cancellation, response limits and generic errors through an HTTP transport fake. No network calls. |
| Corpus ingestion and search | Retrieval service absent; PostgreSQL tests failed to collect. | Real exact ranking/ties/filters, replay/mismatch, version changes, concurrent identical/conflicting imports, atomic rollback and cross-principal RLS denial. |
| Retrieval HTTP route | Auth/validation tests received 404 before route registration; real API test rejected the missing injected provider argument. | Verified human/agent reads, forbidden system/missing-scope callers, strict input, citations, rate limits and no changes to incident/actions/audit/outbox. |
| Synthetic corpus | Fixture-loader import failed before implementation. | Twelve stable unique resolved sources across six services and four severities; CLI imported and replayed the identical receipt on a disposable database. |
| UI/tool transport | Missing precedent component and advisory proxy; registration tests observed six tools where seven were expected. | On-demand query, escaped source text, score/link/provenance, stale-result clearing, unavailable/empty states, origin enforcement and read-only agent transport. |
| JSON byte bound | An adversarial quote-heavy template was truncated into invalid JSON. | UTF-8 and quote-heavy inputs stay within 8,000 bytes and parse as valid JSON. |
| Credential validation | Blank/control/oversized `SecretStr` settings did not raise. | Explicit bounded printable credential checks pass. |

The seventh tool was registered only after the retrieval endpoint's authorization
and citation tests passed. Instruction-like resolution text was returned as data;
the test verified unchanged incident version/status and zero action/audit/outbox
side effects.

Local gate on 2026-10-12:

- `make check`: generated-client drift, Ruff/ESLint/Prettier, mypy/TypeScript,
  329 backend unit/API/worker tests, 81 frontend tests and 124 PostgreSQL tests.
  The first gate stopped on one test-file formatting issue; after formatting,
  the full gate passed. The PostgreSQL suite includes base/head round trips,
  migration drift, named constraints, forced RLS and real concurrent transactions.
- `WEBMCP_NATIVE_CHROME=1 corepack pnpm --filter @incident-ops/web exec playwright test --config playwright.agent.config.ts`:
  4 passed; 2 duplicate fake/fallback cases intentionally inapplicable to native
  Chrome were skipped. Both the deterministic fake and native Chrome journey
  retrieved citations, opened a source page and completed human approval/audit.
- `corepack pnpm --filter @incident-ops/web test:e2e --workers=1`: 10 passed,
  including accessibility and narrow-screen human workflow regressions.

- `corepack pnpm build`: passed, including the new source pages and API adapters.
  The existing Auth0 dynamic-import warning remains; no dependency was added.

- Gitleaks: no leaks in the working source mirror (including new files and local
  plan/setup documents) or the existing 27-commit Git history.

Runtime configuration remains disabled by default. These local checks do not
claim acceptance of paid OpenAI requests, hosted Auth0, live Slack or remote CI.

# Future severity assistance: research and feature scope

Research date: 2026-09-15. Status: **planned post-v1 extension; no implementation in this change**.

## Recommendation

Build an advisory severity assessor that checks reviewed incident history before asking an LLM for a recommendation. Preserve each assessment and human disposition with provenance, subject to retention rules. Publish only deliberately reviewed cases as reusable knowledge. Reuse an existing assessment only when its incident, evidence, permissions, time validity, and configuration still match.

This separates three responsibilities: PostgreSQL records what happened; retrieval supplies relevant examples; a bounded LLM explains how the current evidence fits the severity rubric. A verified human remains responsible for changing authoritative severity. The extension is Phase 8, after the existing v1 phases, with offline evaluation before operational use.

**The expected benefit remains a hypothesis:** better consistency and explanations, with fewer repeated provider calls. The research establishes failure modes, not that RAG will outperform simpler rules on this application's incidents. The release gate must measure that benefit.

## 1. Current application and phase boundaries

The current [advisory classifier](../../apps/api/src/incident_ops/domain/advisory.py) uses keywords in the title and description:

| Result | Existing rule |
|---|---|
| Critical | Security breach/credential leak, or an outage signal together with broad-impact wording |
| High | Outage signal, payment failure, or 5xx spike |
| Medium | Latency, degraded, or error-rate wording |
| Low | No elevated signal matched |

The existing endpoint returns a recommendation without changing the incident. Stored severity is human-controlled and affects action policy, including eligibility for the first automatic retry. Consequently, an incorrect suggestion can still cause harm if a human accepts it without checking the impact. Advisory status alone does not remove that indirect risk. These are findings from the local implementation and [security model](../security-model.md), not claims about a deployed LLM.

V1's rule-based classifier and Phase 6's LLM-free remediation retrieval stay intact. Phase 6 plans to embed severity and resolution alongside symptoms; that representation is unsuitable for assessing an unknown severity. Phase 8 needs a separate, versioned retrieval purpose built from facts available at triage time. A final label or resolution must not leak into the query or select its own supporting examples.

Before Phase 8 coding, extend the product specification explicitly for this separate advisory workflow and record its contracts in an ADR. [ADR-0005](../adr/0005-portable-rag-embeddings.md) already requires provider/model compatibility, a separate generation port, and an approved data-processing arrangement before real incident data goes to a provider. This plan does not select a provider or authorize sending real records externally.

## 2. Research findings

The following evidence is from primary publications and official documentation. The controls in later sections are our proposed engineering design, not a claim that these publications prescribe this exact system.

### Impact and human judgment

- **Severity needs an organization-specific rubric.** PagerDuty's examples consider affected customers, functional loss, duration, and data exposure, and recommend concrete, metric-based definitions. They are useful dimensions, not a direct mapping to this project's four levels. [PagerDuty severity guidance](https://response.pagerduty.com/before/severity_levels/)
- **Human confirmation can still amplify errors.** Zhou et al. observed substantial human reliance on LLM answers, including answers expressing uncertainty. Their experiments support measuring assisted human decisions rather than counting acceptance clicks as correct labels. [ACL 2024 study](https://aclanthology.org/2024.acl-long.198/)
- **Self-reported confidence is not calibrated probability.** Xiong et al. found overconfidence and inconsistent performance across confidence-elicitation methods and tasks. A generated “95% confident” must not become a product reliability claim. [ICLR 2024 study](https://proceedings.iclr.cc/paper_files/paper/2024/hash/6733cf15e10e2cd1d59af033c3bb8507-Abstract-Conference.html)
- **Predictions can feed back into future data.** Sculley et al. describe direct and hidden feedback loops as a source of ML system debt. We therefore distinguish model output, operational human choice, and independently reviewed reusable labels. [NeurIPS 2015 paper](https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf)

### Retrieval, attacks, and privacy

- **Retrieved text can manipulate a model.** Greshake et al. demonstrated indirect prompt injection through externally supplied content in LLM applications. An incident's prose remains untrusted even when its metadata says a human reviewed it. [Indirect prompt injection research](https://arxiv.org/abs/2302.12173)
- **A small poisoned corpus addition can have a large effect.** PoisonedRAG demonstrated targeted answer manipulation by inserting adversarial passages. Its attack rates describe the evaluated systems, not an estimated failure rate for Incident Ops. [USENIX Security 2025 paper](https://www.usenix.org/system/files/usenixsecurity25-zou-poisonedrag.pdf)
- **Injection defenses have limits.** NIST documents integrity, privacy, and availability attacks through indirect injection. Prompts and input scanners are defense layers, not a guarantee of correct severity judgments. [NIST AI 100-2e2025, section 3.4](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-2e2025.pdf)
- **Embeddings are sensitive derived data.** Morris et al. reconstructed source text from embeddings in their evaluated settings. Storing vectors instead of prose is not anonymization. [EMNLP 2023 embedding inversion paper](https://aclanthology.org/2023.emnlp-main.765/)
- **Access control must cover retrieval.** OWASP identifies permission leakage and poisoned vector content as RAG risks and recommends fine-grained access restrictions. [OWASP LLM08:2025](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
- **Cached output is another access boundary.** OWASP's RAG guidance calls for permission-scoped caches, invalidation on document/permission changes, and bounded lifetimes. It also cautions against caching sensitive output. [OWASP RAG security guidance, section 11](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html#section-11-caching-risks)
- **RLS depends on the database role.** PostgreSQL defaults to denial when RLS is enabled without an applicable policy, but privileged roles can bypass RLS. Worker access must still pass application authorization. [PostgreSQL 18 row security documentation](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)

### Cache correctness and evaluation

- **Semantic similarity is insufficient for answer reuse.** Microsoft's semantic-cache policy documentation warns that similar queries can yield incorrect, outdated, or unsafe cached answers and describes user/group partitioning. This supports starting with exact-context assessment reuse. [Microsoft semantic cache policy](https://learn.microsoft.com/en-us/azure/api-management/llm-semantic-cache-lookup-policy)
- **Expiration alone does not ensure consistency.** Microsoft's cache-aside guidance discusses stale entries, update/invalidation ordering, and inconsistent copies. A live incident needs authoritative version checks in addition to a TTL. [Microsoft cache-aside pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside)
- **Exact vector search avoids approximation error, not relevance error.** pgvector defaults to exact nearest-neighbor search; approximate indexes trade recall for speed and can return fewer filtered matches. The small corpus does not justify another search service or ANN tuning initially. [pgvector documentation](https://github.com/pgvector/pgvector)
- **Well-formed JSON can still be wrong.** Google's structured-output documentation requires application validation and handling of schema-compliant but semantically incorrect output. [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- **Citations and answer correctness are distinct.** ALCE evaluates correctness and citation quality separately; a real source identifier does not establish that its text supports a claim. [EMNLP 2023 ALCE paper](https://aclanthology.org/2023.emnlp-main.398/)
- **Evaluation must account for class imbalance and time.** Scikit-learn explains why aggregate accuracy can mislead on imbalanced classes and why conventional splits can train on the future to evaluate the past. Incident evaluation needs explicit as-of cutoffs and grouped incident families. [Classification metrics](https://scikit-learn.org/stable/modules/model_evaluation.html#balanced-accuracy-score), [temporal validation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

These findings do not justify implementing model-serving caches such as RAGCache. That research caches intermediate model states associated with retrieved knowledge; it does not define reliable incident labels or a correct assessment-result cache. Such infrastructure is outside this extension. [RAGCache paper](https://arxiv.org/abs/2404.12457)

## 3. Product contract

### A. Define what the label means

The product owner must define a versioned `low | medium | high | critical` rubric using observable impact. Capture environment, service/function affected, affected count and denominator, regions, onset and elapsed duration, functional loss, data-integrity/security concerns, workaround, and the source/time of each observation. Unknown values remain unknown. The first release uses operator-entered observations; automatic telemetry integrations are separate scope.

Keep **severity at the observation time**, **maximum severity over the incident**, **reviewed outcome**, and **response priority** distinct. A successful mitigation does not prove that the original escalation was wrong. An observation reviewed after resolution must still have a label justified by evidence available at that observation time. Do not attach a final critical label to every earlier snapshot by default.

The rubric should specify deterministic minimum advisory levels for sufficiently supported facts and how uncertainty is handled. The server checks those conditions. An LLM cannot recommend below a validated minimum; contradictory or missing evidence produces a review-needed result. Do not turn the existing keyword classifier into a hard safety floor: negated or historical phrases can trigger it incorrectly.

### B. Build reviewed knowledge without a feedback loop

Retain assessment and review events for audit under the retention policy. Corpus publication is a separate human curation command, not an automatic side effect of incident creation, closing, or accepting an LLM suggestion.

Each published case revision contains:

- Originating incident and duplicate-family identifiers; source incident version; observation timestamp and the time the case became available for retrieval.
- Bounded, redacted triage-time facts; service/environment; explicit unknowns and fact provenance.
- Human-reviewed severity for those facts, rubric version, concise justification, reviewer identity/time, and whether the reviewer saw a model suggestion.
- Server-owned publication state and revision, visibility constraints, content hash, retention classification, and supersession/revocation history.

Use a lifecycle such as `draft -> reviewed -> published -> superseded/revoked`; corrections create revisions. A normal operational confirmation is not independent label validation. Critical cases and disputed labels need independent review and adjudication before publication. Existing seed postmortems need explicit conversion and review before becoming triage exemplars.

### C. Assess current facts against qualified examples

1. Authenticate the human, enforce scopes, and load the current incident and impact snapshot from PostgreSQL.
2. Check exact-context reuse under current authorization and validity. If it misses, reserve a bounded assessment job.
3. Retrieve only published, currently authorized, rubric-compatible cases. Use a separate severity-assessment embedding template over triage symptoms and impact. Exclude the target incident and its duplicate family. Never use current/guessed severity as a retrieval filter.
4. Select up to 3-5 useful sources with an empirically chosen relevance threshold and bounded context size. Do not force a full result count. Limit each incident family to one contribution. Show material differences in impact, not just shared wording.
5. Supply the model with the current fact snapshot, the server-owned rubric, and selected historical facts plus their reviewed labels. Historical labels are examples supplied **after** relevance selection; target severity, target resolution, and later target facts are excluded. Model and embedding ports are separate; the model has no tools or executable destinations.
6. Validate the bounded structured result, cited source revisions, current-fact references, and rubric constraints. Revalidate authorization and all versions after inference. Return a recommendation or an explicit reason for abstention/unavailability.
7. Display the current authoritative severity separately from the suggestion. A human can confirm, correct, or dismiss it. A severity change uses the existing domain rules inside the human review transaction; no model output approves an action request.

No precedent is not evidence of low severity. With no useful matches, show `no_comparable_history`; a supported deterministic rubric warning may still be shown with its actual method. If current facts are insufficient, return `needs_information`. If evidence conflicts, return `needs_review`. Provider failure is `unavailable`, with manual triage usable. Do not silently label a rules fallback as an LLM result or substitute an expired cached answer.

### D. Return inspectable results

Persist a server-generated assessment ID and status; advisory severity or null; method; short rationale; current-fact references; cited case revision IDs; missing information and conflicts; incident/observation versions; rubric/prompt/model/retriever/embedding versions; corpus generation; created/expiry timestamps; and cache-reuse provenance. Keep review state separately versioned. Reject unknown fields, unsupported labels, invented citation IDs, oversized output, and malformed/refused/truncated provider responses.

Display “Suggested severity,” evidence time, relevant differences, missing facts, and whether the result is stale. Show citations through internal authorized links and render prose as plain text. Do not display model-written probability scores or chain-of-thought. A citation check proves identifier validity; evidence support and severity correctness require separate evaluation.

## 4. Storage, authorization, and transactions

### Logical records and service boundaries

Plan for impact snapshots, reviewed case revisions, purpose-specific embeddings, assessment jobs/results, human reviews, and disposable exact-result cache entries with source dependencies and corpus generations. Final table layout belongs in the Phase 8 ADR; reuse existing tables where semantics fit. PostgreSQL remains authoritative and the initial implementation needs no Redis, separate vector store, or training service.

Each new domain table requires default-deny RLS and explicit policies, including vectors, cache dependencies, jobs, reviews, and derived results. Application services perform authorization even when a worker uses a privileged role. Derived results inherit all relevant source restrictions: hiding a citation does not make a rationale safe to disclose. Recheck access before retrieval, provider submission, result delivery, and review. Permission checks that cannot complete fail closed.

The initial flow is human-only. Proposed new scopes, to settle in the ADR, are `severity:assess`, `severity:review`, and `precedents:curate`, combined with existing incident read/write requirements. They must not be added to agent tokens implicitly. This single-organization project still needs cross-principal tests; multi-tenancy is not being introduced.

### Proposed API and execution flow

- Preserve `POST /incidents/{id}/severity-recommendation` as the existing read-scoped rule-based query.
- Add `POST /incidents/{id}/severity-assessments` as a persisted command: expected incident/observation versions and `Idempotency-Key` are required. Return a job/result ID. Accept no caller-supplied actor, approval flag, prompt, provider, arbitrary source ID list, or URL to fetch.
- Read status/results through an authorized assessment query. A completed job can be historical without being valid as a current recommendation.
- Add a human review command bound to the assessment ID, expected assessment and incident versions, disposition, chosen severity where applicable, bounded reason, and idempotency key. A confirm/correct operation that changes severity commits the review, incident mutation, audit, idempotency result, and any required escalation outbox row in one transaction. A dismiss operation records only its permitted review effects.
- Add narrow, versioned human curation commands for case publication, correction, and revocation; use the same command framework. Publication metadata comes from the verified actor and server, never incident prose.

Reserve a job, its audit/idempotency record, and any work notification transactionally. Provider calls occur outside database transactions. Use finite leases and fencing so a worker that lost ownership cannot complete a job. Assessment work needs a distinct dispatch type from Slack delivery; do not enqueue an unknown payload into the Slack-only worker. On completion, lock and revalidate incident, observations, corpus/configuration generations, source revisions, and requesting-principal authorization before atomically saving the result and eligible cache entry.

Retries are bounded and safe at the persistence boundary. An external provider may still bill duplicate requests after an ambiguous timeout; exactly-once generation/billing is not promised. Reserve budgets atomically, limit concurrency per principal and globally, cap tokens and attempts, and define provider timeout/circuit-breaker behavior. Cancelling a browser request cannot retract a provider request already sent. Cancelling or superseding a job prevents late results from becoming current.

### Sensitive data lifecycle

Minimize and redact inputs before both embedding and generation. Free-tier use remains synthetic-only per ADR-0005. Approve provider retention, training use, region, deletion capability, and incident-data classification before real-data processing; verify current terms then. No prompts, raw incident text, secrets, embeddings, or credentials in ordinary logs. Audit operational metadata and hashes; keep any necessary replay snapshots in separately protected, retention-bound storage.

Do not result-cache output containing secrets, personal information, or data classified as non-cacheable. Source correction, redaction, deletion, or access revocation must invalidate affected derived records and prevent delayed jobs from republishing them. Define deletion of sensitive payloads separately from retention of minimal immutable audit metadata; do not promise erasure if raw content remains in an audit event, backup, or provider store. This work requires an explicit post-v1 retention/redaction design, not a generic application delete endpoint.

## 5. Cache design: knowledge first, exact reuse second

| Layer | Purpose | Initial scope |
|---|---|---|
| Reviewed case repository | Durable evidence and human-reviewed labels | Versioned PostgreSQL records; this is knowledge, not a cache |
| Embedding cache | Avoid re-embedding identical eligible text | Hash sanitized content plus purpose, template, provider/model, dimension, and visibility namespace; recheck publication on use |
| Assessment-result cache | Avoid repeating the same authorized assessment | Same principal, incident, evidence snapshot, versions, and time validity only |
| Retrieval-result cache | Avoid repeating a small exact vector query | Deferred; the corpus is initially small |
| Semantic answer cache across incidents | Copy a prior answer based on similarity | Out of scope; similarity retrieves examples, never grants answer reuse |

### Exact cache identity and admission

Use a canonical, server-generated fingerprint covering:

- Cache schema/purpose; principal ID and actor type; effective server-verified access context and authorization generation.
- Incident ID/version; observation IDs/versions and sanitized fact hashes; assessment as-of time/freshness interval, including time-derived facts.
- Rubric, prompt, output schema, redaction, generator provider/exact model or controlled deployment revision, and generation settings.
- Retriever/embedding provider/model/dimension/template versions, selection settings, and the active index/corpus publication generation.
- Ordered selected source IDs, revisions, and content hashes as recorded dependencies.

A hash is an identity aid, not authorization. Never put bearer tokens in a key. The lookup can use the request fingerprint plus current corpus/configuration generations, then verify stored source dependencies; it need not call the model or repeat retrieval merely to check reuse. Use an organization-wide corpus generation initially for conservative invalidation. It also catches a newly published relevant case that was absent from the previous source list.

Only validated, currently authorized, fresh results are cache-admissible. Refusals, errors, budget exhaustion, invalid output, and stale results do not become reusable recommendations. A retried idempotency key returns the existing command/job identity, not an unconditionally fresh answer; every response still checks permissions and labels expired results as historical/unavailable.

### Freshness and invalidation

Proposed starting TTL: **at most 60 seconds** for an active incident, additionally bounded by the earliest fact expiry or time-dependent rubric boundary. This is a conservative engineering default to validate, not a research-derived threshold. Hits do not extend expiry. Advancing duration can invalidate an assessment even without a text edit or incident version change. Human review always revalidates current facts and versions.

Invalidate on incident/observation edits; case correction, reopening, publication, supersession or revocation; source permission changes; rubric/configuration/model changes; redaction/deletion; and time validity. Pin model versions where supported; if a provider exposes only a mutable alias, use a controlled deployment generation and disable reuse across uncertain changes. Do not normalize away negations, numerical units, regions, or service/environment distinctions to improve hit rate.

Bump authoritative corpus generations in the same transaction as publication/revocation changes. Compare them when a worker completes and whenever a cached result is served or reviewed. A stale worker cannot repopulate an invalidated entry. Coalesce simultaneous identical requests with a unique key and finite lease; do not coalesce different principals or versions. Rechecking only the old citations misses new evidence, and deleting a cache row without fencing lets a delayed job recreate it.

On cache failure, use a fresh bounded assessment or the explicit manual/unavailable path. Never serve stale results to hide a provider outage. Monitor cache hit/miss/invalidation reasons and saved provider calls without logging source content. Disable result caching entirely when correctness or data-classification requirements cannot be met.

## 6. Edge-case register and required proof

These are concrete project test scenarios derived from the research and code review. They describe acceptance requirements for future implementation, not tests already run or guarantees about every possible attack.

### Impact, labels, and retrieval

| ID | What can go wrong | Required behavior/proof |
|---|---|---|
| Q1 | Identical timeout text describes one staging user and most production checkout traffic. | Paired fixtures change only impact/environment. The assessor must account for that change or abstain; no answer-cache reuse across incidents. |
| Q2 | Credential exposure or data corruption occurs while availability is healthy. | Critical sentinel cases exercise security/integrity dimensions independently of outage similarity. Supported severe facts cannot become a silent low suggestion. |
| Q3 | “Payments failing” omits denominator, environment, duration, and recovery information. | Unknowns remain explicit; ask for missing facts or manual review. Never fill unknown counts with zero. |
| Q4 | “No data loss,” a quoted old outage, sarcasm, mixed language, or unfamiliar jargon fools text matching. | Test negation, attribution, historical statements, Unicode, and supported languages. Unsupported language/ambiguous extraction needs review. |
| Q5 | The title says all users, but a newer observation reports one region. | Preserve source/time and expose conflict; do not quietly choose the fact yielding the easiest answer. |
| Q6 | Duration crosses a rubric threshold without an incident edit; observations arrive late or out of order. | Evaluate using authoritative UTC observation/as-of times, validate clock/order bounds, and expire at the next relevant boundary. |
| Q7 | Final resolution/peak severity leaks into an early assessment or embedding. | Snapshot tests exclude target labels and later facts from query, retrieval representation, and generation input; historical labels remain tied to their own observation time. |
| Q8 | A relevant incident happened earlier but was not reviewed/published until after a replayed assessment. | Replayed retrieval respects publication availability, not just incident creation time. |
| Q9 | Fifty duplicate reports crowd out one informative case or appear on both sides of an evaluation split. | Group incident families before splitting; exclude the target family and limit family contribution in retrieval. |
| Q10 | Similar cases have contradictory reviewed labels, different impact, or incompatible old rubrics. | Exclude incompatible rubrics unless explicitly rereviewed; show differences and abstain on unresolved contradiction. No label majority vote. |
| Q11 | One service records all minor incidents while another only records major outages. | Report class/service coverage; evaluate held-out services and skewed corpora. Record frequency cannot establish low impact. |
| Q12 | Corpus is empty, all candidates are irrelevant, a service is new, or there are fewer than k authorized matches. | Return no/limited comparable history. Never pad context with weak or forbidden matches, and preserve manual/structured-rubric warnings. |
| Q13 | A model cites a real incident but invents its impact or transfers its user count to the current incident. | Check source IDs deterministically and evaluate claim support separately. Distinguish current observations from historical analogies in the UI. |
| Q14 | An accepted suggestion becomes its own label; intervention prevents the predicted escalation. | Preserve model exposure and review provenance. Require separate corpus curation; judge the original decision using then-available facts. |

### Security and privacy

| ID | What can go wrong | Required behavior/proof |
|---|---|---|
| S1 | Current/retrieved prose asks the model to ignore the rubric, downgrade incidents, leak a secret, or invoke a tool. | Direct, hidden, encoded, and multi-record injection fixtures. Server boundaries prevent actions/external destinations; assess residual severity manipulation as a model-quality failure. |
| S2 | A record contains forged reviewer metadata/source IDs or poisoned low-severity examples. | Metadata is server-owned; publication requires curation. Exercise near-duplicate poisoning and benign-looking false labels, not just instruction scanners. |
| S3 | Principal A warms a cache using history unavailable to principal B. | B receives no restricted result, rationale, snippet, identifier, or source count. Test same-organization scope differences and actor-type changes. |
| S4 | Access is revoked during inference, after a cache hit, or before human confirmation. | Revalidate at completion/read/review and deny stale authority. A provider request already transmitted cannot be unsent; document that residual limit. |
| S5 | The worker role bypasses RLS, a body spoofs the actor, or an agent requests curation/review. | Application authorization plus PG role/RLS tests deny each path; unknown command fields are rejected. |
| S6 | Incident text includes credentials, personal data, HTML, remote-image markup, or Slack mentions. | Inspect fake-provider payloads and persisted outputs for minimization; test safe plain-text rendering, no remote fetch, and mention suppression where notification formatting applies. |
| S7 | Source deletion/redaction leaves a vector, recommendation, log, or late ingestion write behind. | Test dependency invalidation and tombstones; delayed workers cannot republish. Verify retention handling across derived payloads; minimal audit metadata must contain no raw secret. |

### Cache, concurrency, and availability

| ID | What can go wrong | Required behavior/proof |
|---|---|---|
| C1 | A human corrects a cited case or publishes a newly relevant case; old cited revisions alone still appear valid. | Both revision dependencies and corpus generation cause misses. Test additions as well as edits/removals. |
| C2 | The incident changes mid-inference, a lease expires, or two reviewers act simultaneously. | Late results stay stale; fencing and optimistic versions allow one valid completion/review. Audit/idempotency/outbox effects remain atomic. |
| C3 | An idempotency replay returns an old response after expiry/revocation; the same key is reused with another body. | Preserve command identity without bypassing freshness/access; reject mismatched payloads and mark stale historical results. |
| C4 | Embedding models share a dimension but occupy different spaces; the generator alias changes. | Reject mixed embedding spaces/purposes; controlled re-embedding and configuration generations invalidate affected results. |
| C5 | A restart, cache miss burst, or unavailable provider creates duplicate requests and uncontrolled cost. | Finite leases, coalescing, atomic budget reservations, bounded attempts/concurrency, timeouts, and circuit breaker; manual path remains usable. |
| C6 | Provider returns malformed JSON, unsupported severity, invented IDs, refusal, excessive output, or partial output. | Fail typed validation; never cache/apply the answer. Return a generic unavailable/invalid-result status without provider secrets. |
| C7 | Large histories consume the context budget and truncate the decisive impact fact. | Bound records and total tokens; prioritize required facts. If a complete valid context cannot fit, abstain rather than silently discard required evidence. |
| C8 | Confirmation would lower stored severity and unlock a retry; another mutation occurs first. | Confirmation stays a human incident command with current-version checks. Action policy re-evaluates stored state separately; assessment/review cannot approve or execute an action. |

## 7. Evaluation and release gates

### Freeze the protocol before tuning

The incident/product owner must specify the rubric, error-cost weights, per-class minimum sample counts, numerical success thresholds, allowable regressions, uncertainty-interval method, latency/cost budgets, and rollback triggers before final holdout evaluation. There is no defensible universal similarity threshold or acceptable critical-miss rate established by these sources for this product.

Use independent human rubric labels with disagreements adjudicated. Record whether each reviewer saw a model answer. A temporal holdout must group related incidents and reconstruct both facts and knowledge available at each assessment time. Keep tuning data separate from the final test set. Explicit timestamp cutoffs suit irregular incidents better than mechanically applying equally spaced time-series splitting.

The 10-20 seeded v1 cases prove plumbing and demonstrations only. They cannot establish production safety or representative critical-incident performance. If required slices lack the predeclared sample count, the result is **insufficient evidence for rollout**, not a pass.

### Compare approaches on identical snapshots

Evaluate existing keyword rules, a structured deterministic rubric, a retrieval-only baseline, an LLM with the rubric but no history, and the proposed rubric-plus-RAG assessor. Measure the same inputs with and without caching. This distinguishes the value of better impact data, the LLM, retrieval, and reuse. Keep a no-cache reference path in evaluation and investigate any validity/output difference caused by caching.

| Dimension | Measures and gate |
|---|---|
| Severity quality | Full ordered confusion matrix; per-class precision/recall and counts; macro metrics; ordinal error distance; predeclared weighted under/over-triage cost. Report critical-to-low/medium and high-to-low errors separately. |
| Uncertainty | Coverage versus error among answered cases, abstention reasons, required-subgroup results, and uncertainty intervals. Abstaining on everything cannot pass the usefulness gate. |
| Retrieval | Reviewer-judged Recall@k/ranking relevance; useful-precedent coverage; duplicate-family contribution; correct behavior with no matches. Similarity is not a probability of severity. |
| Grounding | Authorized supplied citation membership as a deterministic invariant; claim support and citation completeness independently adjudicated. An LLM judge may help triage evaluations but is not the sole label authority. |
| Security/correctness | Zero forbidden mutations, unauthorized data disclosures, cross-principal reuse, stale-current results, or invalid citation IDs in the specified boundary suite; all critical sentinels trigger a supported warning or review. Passing tests does not prove zero real-world risk. |
| Operations | p50/p95 latency, provider failure/retry/abstention rates, tokens and cost per assessment, cache effectiveness and invalidation reasons, and budget-cap behavior. |
| Human usefulness | In a controlled pilot, compare unaided and assisted decisions to independently adjudicated labels: under-triage, incorrect acceptance, overrides, evidence inspection, and decision time. Acceptance rate alone is not success. |

RAG must improve the predeclared objective over the strongest relevant baseline without an unaccepted critical-safety regression. If it does not, keep the simpler approach and retain the reviewed-history UI where useful. Numerical thresholds remain an implementation-entry decision because the project does not yet have a labeled operational corpus.

### Roll out gradually

1. Disabled by default; deterministic fake providers and synthetic fixtures in normal CI. A separately controlled evaluation command uses a selected real provider only after its data/budget prerequisites are settled.
2. Offline held-out evaluation and all adversarial/concurrency/permission tests pass.
3. Shadow mode collects suggestions without showing them before the operational human decision. Record suggestions and independent labels separately.
4. Limited human-review pilot with the approved rubric, explicit stale/unknown states, monitored budgets, and a kill switch. Manual triage remains available throughout.
5. Expand only after the predeclared human and model quality gates pass. Re-evaluate model, prompt, rubric, embedding, retriever, and corpus-policy changes; track ordinary corpus revisions and rerun affected regression/sentinel cases.

Rollback disables generation/result-cache use and leaves the ordinary human workflow and clearly labeled rules available. A confirmed bad source revokes its derived recommendations immediately. Repeated critical misses, unauthorized exposure, invalidation failures, or budget/latency breaches trigger the predeclared stop conditions.

## 8. Delivery scope and unresolved decisions

The implementation plan divides this extension into 8A (rubric/contracts/data/evaluation protocol), 8B (reviewed case lifecycle and retrieval), 8C (bounded assessor and human review), 8D (exact caching and invalidation), and 8E (evaluation and rollout). Every stage retains the existing backend ownership, RLS, idempotency, concurrency, and transaction gates.

**Included:** triage snapshots, human-reviewed case publication, a separate severity retrieval representation, advisory suggestion/abstention, human disposition, bounded jobs, audit provenance, exact-context reuse, correction/revocation, offline evaluation, and a staged pilot.

**Deferred:** automatic severity changes, autonomous remediation/escalation, agent access to the new workflow, automatic publication of every result, fine-tuning/online learning, similarity-only answer reuse across incidents, telemetry connectors, imported external knowledge, multi-tenancy, an additional vector service, and model-internal cache infrastructure.

Before coding, settle:

- Rubric/label semantics, required observations, supported languages, and critical-case review rules; owner: incident/product owner.
- Scope names, source visibility, curation/revocation and retention contracts, provider/data classification, and audit payload design; owner: application/security owner.
- Provider/model/version strategy, token/latency/cost limits, retry policy, and TTL/freshness boundaries; owner: engineering owner.
- Dataset adequacy, numerical quality targets, minimum sample sizes, independent review, pilot design, and rollback thresholds; owner: product and evaluation owners.

These are entry gates for the future phase, not requests to block current v1 development. The original project specification remains authoritative until explicitly extended for Phase 8.

## Methodology and limits

Investigated five questions: how severity should be grounded in impact; how reviewed history can become reliable evidence; when cached judgments remain valid; how retrieval/generation can violate security or privacy; and how to measure benefit without leakage or human feedback bias. Parallel security and evaluation research was reconciled with local classifier, policy, phase-plan, and ADR review. Used web search and primary-source pages/papers; the preferred Firecrawl/Exa tools were unavailable in this session.

The report cites 19 primary sources: official technical/incident-response guidance, original research publications, and NIST's attack taxonomy. Key cache, RLS, structured-output, incident-rubric, and security guidance was read beyond search snippets. Older research is included for established failure mechanisms; model-specific historical benchmark results are not extrapolated into current product accuracy. Instruction-like strings encountered in security publications were attack examples and were treated only as source data.

No live incident data was uploaded and no model benchmark was run for this planning work. There is insufficient evidence to choose a best provider/model, promise a cache hit rate, quantify accuracy gains, or claim a safe universal threshold. Those uncertainties are carried into the Phase 8 entry and release gates rather than presented as validated results.

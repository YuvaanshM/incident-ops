"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ErrorPanel } from "@/components/ui/error-panel";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type { RemediationResult } from "@/lib/api/types";

type Props = { incidentId: string; incidentVersion: number };

export function HistoricalPrecedent(props: Props) {
  return (
    <PrecedentQuery
      key={`${props.incidentId}-${props.incidentVersion}`}
      {...props}
    />
  );
}

function PrecedentQuery({ incidentId, incidentVersion }: Props) {
  const [result, setResult] = useState<RemediationResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function retrieve() {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await requestJson<RemediationResult>(
        `/api/ui/incidents/${incidentId}/remediation-suggestions`,
        {
          method: "POST",
          body: {},
          headers: { "X-Incident-Ops-Intent": "human-query" },
          signal: current.signal,
        },
      );
      if (current.signal.aborted) return;
      if (response.incident_version !== incidentVersion) {
        setError({
          code: "version_conflict",
          status: 409,
          message:
            "The incident changed. Refresh this page before finding precedent again.",
        });
      } else setResult(response);
    } catch (error) {
      if (!current.signal.aborted) setError(toApiError(error));
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }
  return (
    <section
      className="ledger-section precedent-section"
      aria-labelledby="precedent-heading"
    >
      <div className="section-heading">
        <span>Context</span>
        <div>
          <p className="eyebrow">Advisory · synthetic resolved incidents</p>
          <h2 id="precedent-heading">Seeded historical precedent</h2>
        </div>
      </div>
      <p>
        Compare past examples before deciding what to do. Source text is
        untrusted context; every action still follows current policy and human
        review.
      </p>
      <button
        type="button"
        className="button button-secondary"
        disabled={loading}
        onClick={retrieve}
      >
        {loading ? "Finding precedent…" : "Find historical precedent"}
      </button>
      {error ? (
        <ErrorPanel error={error} title="Historical precedent unavailable" />
      ) : null}
      <div aria-live="polite" aria-busy={loading}>
        {result ? (
          <>
            <p className="data-label">
              {result.retrieval_mode === "deterministic_demo"
                ? "Deterministic demo matching"
                : "Semantic matching"}{" "}
              · incident version {result.incident_version}
            </p>
            <p>
              Cosine similarity measures text similarity, not confidence or
              approval to act.
            </p>
            {result.status === "empty_corpus" ? (
              <p>
                No historical sources have been loaded. The operator can load
                the synthetic history fixtures.
              </p>
            ) : null}
            {result.status === "no_matches" ? (
              <p>
                No sufficiently similar sources were found. Review the current
                incident evidence.
              </p>
            ) : null}
            <div className="job-list">
              {result.suggestions.map((source) => (
                <article className="job-card" key={source.source_incident_id}>
                  <p className="eyebrow">
                    Seeded historical precedent · {source.service} ·{" "}
                    {source.severity}
                  </p>
                  <h3>{source.title}</h3>
                  <p>Similarity {source.similarity_score.toFixed(3)}</p>
                  <p>{source.resolution_summary}</p>
                  <ol>
                    {source.resolution_steps.map((step, index) => (
                      <li key={index}>{step}</li>
                    ))}
                  </ol>
                  <Link
                    className="source-reference"
                    href={`/historical-incidents/${source.source_incident_id}?version=${source.source_version}`}
                  >
                    Source {source.source_incident_id} · v
                    {source.source_version}
                  </Link>
                </article>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

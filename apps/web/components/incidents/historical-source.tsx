"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorPanel, LoadingLedger } from "@/components/ui/error-panel";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type { HistoricalSource } from "@/lib/api/types";

export function HistoricalSourceScreen({
  sourceId,
  citedVersion,
}: {
  sourceId: string;
  citedVersion: string | undefined;
}) {
  const [source, setSource] = useState<HistoricalSource | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    requestJson<HistoricalSource>(
      `/api/ui/historical-incidents/${encodeURIComponent(sourceId)}`,
      { signal: controller.signal },
    )
      .then((source) => {
        if (!controller.signal.aborted) setSource(source);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setError(toApiError(error));
      });
    return () => controller.abort();
  }, [sourceId]);
  if (error)
    return <ErrorPanel error={error} title="Historical source unavailable" />;
  if (!source) return <LoadingLedger label="Loading historical source" />;
  return (
    <div className="page-stack">
      <nav aria-label="Breadcrumb" className="breadcrumbs">
        <Link href="/incidents">Incidents</Link>
        <span>/</span>
        <span>Historical source</span>
      </nav>
      <article className="ledger-section">
        <p className="eyebrow">
          Seeded historical precedent · resolved synthetic incident
        </p>
        <h1>{source.title}</h1>
        <p className="source-reference">
          Source {source.source_incident_id} · v{source.source_version}
        </p>
        {citedVersion && citedVersion !== String(source.source_version) ? (
          <p role="status">
            This source has changed since the cited version. The current source
            is shown below.
          </p>
        ) : null}
        <p>
          {source.service} · {source.severity}
        </p>
        <p>{source.description}</p>
        <h2>Recorded resolution</h2>
        <p>{source.resolution_summary}</p>
        <ol>
          {source.resolution_steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
        <p>
          Advisory source text. Review current incident evidence and policy
          before requesting an action.
        </p>
      </article>
    </div>
  );
}

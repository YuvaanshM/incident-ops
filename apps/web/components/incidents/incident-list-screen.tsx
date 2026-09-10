"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  SeverityBadge,
  StatusBadge,
  VersionBadge,
} from "@/components/ui/badges";
import { ErrorPanel, LoadingLedger } from "@/components/ui/error-panel";
import { Timestamp } from "@/components/ui/timestamp";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type { Incident, IncidentFilters, IncidentPage } from "@/lib/api/types";

function listUrl(filters: IncidentFilters, cursor?: string): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.service) params.set("service", filters.service);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.status) params.set("status", filters.status);
  if (cursor) params.set("cursor", cursor);
  params.set("limit", "20");
  return `/api/ui/incidents?${params.toString()}`;
}

function deduplicate(items: Incident[]): Incident[] {
  return [...new Map(items.map((item) => [item.id, item])).values()];
}

function IncidentCells({
  item,
  table = false,
}: {
  item: Incident;
  table?: boolean;
}) {
  const role = table ? "cell" : undefined;
  return (
    <>
      <div className="incident-severity-cell" role={role}>
        <SeverityBadge severity={item.severity} />
      </div>
      <div className="incident-main-cell" role={role}>
        <Link className="incident-title-link" href={`/incidents/${item.id}`}>
          {item.title}
        </Link>
        <p>{item.description}</p>
        <span className="service-name">{item.service}</span>
      </div>
      <div role={role}>
        <StatusBadge status={item.status} />
      </div>
      <div role={role}>
        <Timestamp value={item.updated_at} />
      </div>
      <div role={role}>
        <VersionBadge version={item.version} />
      </div>
    </>
  );
}

export function IncidentListScreen({
  initialFilters,
}: {
  initialFilters: IncidentFilters;
}) {
  const [items, setItems] = useState<Incident[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const initialUrl = listUrl(initialFilters);

  useEffect(() => {
    const controller = new AbortController();
    void requestJson<IncidentPage>(initialUrl, {
      signal: controller.signal,
    })
      .then((page) => {
        setItems(deduplicate(page.items));
        setNextCursor(page.next_cursor);
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) setError(toApiError(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt, initialUrl]);

  const loadMore = useCallback(() => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    const controller = new AbortController();
    const cursorUrl = new URL(initialUrl, "http://incident-ops.local");
    cursorUrl.searchParams.delete("limit");
    cursorUrl.searchParams.set("cursor", nextCursor);
    cursorUrl.searchParams.set("limit", "20");
    const path = `${cursorUrl.pathname}?${cursorUrl.searchParams.toString()}`;
    void requestJson<IncidentPage>(path, {
      signal: controller.signal,
    })
      .then((page) => {
        setItems((current) => deduplicate([...current, ...page.items]));
        setNextCursor(page.next_cursor);
      })
      .catch((requestError: unknown) => setError(toApiError(requestError)))
      .finally(() => setLoadingMore(false));
  }, [initialUrl, loadingMore, nextCursor]);

  return (
    <div className="page-stack">
      <header className="page-heading page-heading-action">
        <div>
          <p className="eyebrow">Incident ledger</p>
          <h1>Active incidents</h1>
          <p className="page-intro">
            The authoritative record of current and recently completed response
            work.
          </p>
        </div>
        <Link className="button button-primary" href="/incidents/new">
          <span aria-hidden="true">＋</span> New incident
        </Link>
      </header>

      <form
        action="/incidents"
        className="filter-bar"
        method="get"
        role="search"
      >
        <label className="field filter-search">
          <span>Search incidents</span>
          <input
            defaultValue={initialFilters.q}
            name="q"
            placeholder="Title or description"
          />
        </label>
        <label className="field">
          <span>Service</span>
          <input
            defaultValue={initialFilters.service}
            name="service"
            placeholder="Any service"
          />
        </label>
        <label className="field">
          <span>Severity</span>
          <select defaultValue={initialFilters.severity ?? ""} name="severity">
            <option value="">Any severity</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </label>
        <label className="field">
          <span>Status</span>
          <select defaultValue={initialFilters.status ?? ""} name="status">
            <option value="">Any status</option>
            <option value="open">Open</option>
            <option value="investigating">Investigating</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
        </label>
        <div className="filter-actions">
          <button className="button button-primary" type="submit">
            Apply
          </button>
          <Link className="text-link" href="/incidents">
            Clear
          </Link>
        </div>
      </form>

      {loading ? <LoadingLedger /> : null}
      {!loading && error ? (
        <ErrorPanel
          error={error}
          onRetry={() => {
            setError(null);
            setLoading(true);
            setAttempt((value) => value + 1);
          }}
        />
      ) : null}
      {!loading && !error && items.length === 0 ? (
        <section className="empty-ledger">
          <span aria-hidden="true" className="empty-ledger-glyph">
            Ø
          </span>
          <h2>No incidents match these filters</h2>
          <p>
            Clear a filter or create a new incident to begin the response
            record.
          </p>
        </section>
      ) : null}

      {!loading && items.length > 0 ? (
        <>
          <div aria-label="Incidents" className="incident-table" role="table">
            <div className="incident-table-head" role="row">
              <span role="columnheader">Severity</span>
              <span role="columnheader">Incident / service</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">Updated</span>
              <span role="columnheader">Version</span>
            </div>
            {items.map((item) => (
              <div
                className={`incident-row severity-rail-${item.severity}`}
                key={item.id}
                role="row"
              >
                <IncidentCells item={item} table />
              </div>
            ))}
          </div>
          <div aria-label="Incidents" className="incident-card-list">
            {items.map((item) => (
              <article
                className={`incident-card severity-rail-${item.severity}`}
                key={item.id}
              >
                <IncidentCells item={item} />
              </article>
            ))}
          </div>
        </>
      ) : null}

      {nextCursor ? (
        <div className="pagination-row">
          <span>{items.length} incidents shown</span>
          <button
            className="button button-secondary"
            disabled={loadingMore}
            onClick={loadMore}
            type="button"
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

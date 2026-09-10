"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ActionStatusBadge } from "@/components/ui/badges";
import { ErrorPanel, LoadingLedger } from "@/components/ui/error-panel";
import { Timestamp } from "@/components/ui/timestamp";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type {
  Action,
  ActionPage,
  ActionStatus,
  Incident,
} from "@/lib/api/types";

const actionLabels: Record<Action["action_type"], string> = {
  retry_failed_job: "Retry failed job",
};

function approvalUrl(status: ActionStatus | "", cursor?: string): string {
  const query = new URLSearchParams({ limit: "20" });
  if (status) query.set("status", status);
  if (cursor) query.set("cursor", cursor);
  return `/api/ui/actions?${query.toString()}`;
}

function deduplicate(items: Action[]): Action[] {
  return [...new Map(items.map((item) => [item.id, item])).values()];
}

export function actionLabel(actionType: Action["action_type"]): string {
  return actionLabels[actionType] ?? humanize(actionType);
}

export function humanize(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function ApprovalQueueScreen({
  initialStatus,
}: {
  initialStatus: ActionStatus | "";
}) {
  const [items, setItems] = useState<Action[]>([]);
  const [incidents, setIncidents] = useState<Record<string, Incident>>({});
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const initialUrl = useMemo(() => approvalUrl(initialStatus), [initialStatus]);

  async function loadIncidentContext(actions: Action[], signal?: AbortSignal) {
    const missingIds = [
      ...new Set(
        actions
          .map((action) => action.incident_id)
          .filter((id) => !incidents[id]),
      ),
    ];
    const options = signal ? { signal } : {};
    const records = await Promise.all(
      missingIds.map((id) =>
        requestJson<Incident>(`/api/ui/incidents/${id}`, options),
      ),
    );
    setIncidents((current) => ({
      ...current,
      ...Object.fromEntries(records.map((record) => [record.id, record])),
    }));
  }

  useEffect(() => {
    const controller = new AbortController();
    void requestJson<ActionPage>(initialUrl, { signal: controller.signal })
      .then(async (page) => {
        setItems(deduplicate(page.items));
        setNextCursor(page.next_cursor);
        await loadIncidentContext(page.items, controller.signal);
        setError(null);
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) setError(toApiError(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // The incident map is an enrichment cache, not a query dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, initialUrl]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await requestJson<ActionPage>(
        approvalUrl(initialStatus, nextCursor),
      );
      setItems((current) => deduplicate([...current, ...page.items]));
      setNextCursor(page.next_cursor);
      await loadIncidentContext(page.items);
    } catch (requestError) {
      setError(toApiError(requestError));
    } finally {
      setLoadingMore(false);
    }
  }

  const forbidden = error?.status === 403;

  return (
    <div className="page-stack">
      <header className="page-heading approval-heading">
        <div>
          <p className="eyebrow">Human decision queue</p>
          <h1>Approval desk</h1>
          <p className="page-intro">
            Review the reason, policy, and incident record before authorizing a
            bounded operation.
          </p>
        </div>
        <div aria-label="Approval principle" className="approval-principle">
          <span>Human gate</span>
          <strong>No silent execution</strong>
        </div>
      </header>

      <form
        action="/approvals"
        className="approval-filter"
        method="get"
        role="search"
      >
        <label className="field">
          <span>Action status</span>
          <select defaultValue={initialStatus} name="status">
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="applied">Applied</option>
            <option value="rejected">Rejected</option>
            <option value="failed">Failed</option>
            <option value="stale">Stale</option>
          </select>
        </label>
        <button className="button button-primary" type="submit">
          Apply filter
        </button>
        <Link className="text-link" href="/approvals">
          Clear
        </Link>
      </form>

      {loading ? <LoadingLedger label="Loading approval requests" /> : null}
      {!loading && error ? (
        <ErrorPanel
          error={error}
          {...(forbidden
            ? {}
            : {
                onRetry: () => {
                  setError(null);
                  setLoading(true);
                  setAttempt((value) => value + 1);
                },
              })}
          title={
            forbidden
              ? "Approval access required"
              : "The approval desk is unavailable"
          }
        />
      ) : null}
      {!loading && !error && items.length === 0 ? (
        <section className="empty-ledger">
          <span aria-hidden="true" className="empty-ledger-glyph">
            ✓
          </span>
          <h2>No approval requests match this filter</h2>
          <p>There is nothing waiting for a human decision in this view.</p>
        </section>
      ) : null}

      {!loading && !error && items.length > 0 ? (
        <div aria-label="Approval requests" className="approval-ledger">
          <div aria-hidden="true" className="approval-ledger-head">
            <span>Request</span>
            <span>Incident context</span>
            <span>State</span>
            <span>Requested</span>
          </div>
          {items.map((action, index) => {
            const incident = incidents[action.incident_id];
            return (
              <article className="approval-row" key={action.id}>
                <span aria-hidden="true" className="approval-index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="approval-action-cell">
                  <Link
                    className="approval-title"
                    href={`/approvals/${action.id}`}
                  >
                    {actionLabel(action.action_type)}
                  </Link>
                  <p>{action.rationale || "No rationale supplied."}</p>
                  <span className="mono-note">{action.id.slice(0, 8)}</span>
                </div>
                <div className="approval-incident-cell">
                  {incident ? (
                    <>
                      <Link href={`/incidents/${incident.id}`}>
                        {incident.title}
                      </Link>
                      <span>{incident.service}</span>
                    </>
                  ) : (
                    <span>Incident context unavailable</span>
                  )}
                </div>
                <ActionStatusBadge status={action.status} />
                <Timestamp exact value={action.created_at} />
              </article>
            );
          })}
        </div>
      ) : null}

      {nextCursor && !error ? (
        <div className="pagination-row">
          <span>{items.length} requests shown</span>
          <button
            className="button button-secondary"
            disabled={loadingMore}
            onClick={loadMore}
            type="button"
          >
            {loadingMore ? "Loading…" : "Load more requests"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

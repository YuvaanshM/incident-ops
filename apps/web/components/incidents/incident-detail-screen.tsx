"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ActionStatusBadge,
  SeverityBadge,
  StatusBadge,
  VersionBadge,
} from "@/components/ui/badges";
import { ErrorPanel, LoadingLedger } from "@/components/ui/error-panel";
import { Timestamp } from "@/components/ui/timestamp";
import { Timeline } from "@/components/timeline/timeline";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type {
  Action,
  DemoJob,
  Incident,
  IncidentStatus,
  TimelinePage,
} from "@/lib/api/types";

function actionMessage(action: Action): string {
  const messages: Record<Action["status"], string> = {
    pending: "Retry request is pending human review.",
    applied: "The backend applied the retry request.",
    rejected: "The backend rejected the retry request.",
    failed: "The retry request ran but failed.",
    stale: "The retry request became stale before it could run.",
  };
  return messages[action.status];
}

function RetryJobForm({
  incident,
  job,
  onAction,
  onRefresh,
}: {
  incident: Incident;
  job: DemoJob;
  onAction: (action: Action) => void;
  onRefresh: () => void;
}) {
  const [rationale, setRationale] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function requestRetry(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const action = await requestJson<Action>(
        `/api/ui/incidents/${incident.id}/actions`,
        {
          method: "POST",
          mutation: true,
          body: {
            action_type: "retry_failed_job",
            expected_incident_version: incident.version,
            parameters: { job_id: job.id },
            rationale: rationale.trim() || null,
          },
        },
      );
      onAction(action);
      if (action.status !== "pending") onRefresh();
    } catch (requestError) {
      setError(toApiError(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="retry-form" onSubmit={requestRetry}>
      <label className="field">
        <span>Retry rationale</span>
        <input
          aria-label="Retry rationale"
          maxLength={1000}
          onChange={(event) => setRationale(event.target.value)}
          placeholder="Why is another attempt safe now?"
          value={rationale}
        />
      </label>
      {error ? (
        <ErrorPanel error={error} title="The retry was not requested" />
      ) : null}
      <button
        className="button button-secondary"
        disabled={submitting}
        type="submit"
      >
        {submitting ? "Requesting…" : "Request retry"}
      </button>
    </form>
  );
}

function DemoJobCard({
  demoToolsEnabled,
  incident,
  job,
  onAction,
  onRefresh,
}: {
  demoToolsEnabled: boolean;
  incident: Incident;
  job: DemoJob;
  onAction: (action: Action) => void;
  onRefresh: () => void;
}) {
  const [simulating, setSimulating] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function simulateFailure() {
    setSimulating(true);
    setError(null);
    try {
      await requestJson<DemoJob>(
        `/api/ui/demo/jobs/${job.id}/simulate-failure`,
        {
          method: "POST",
          mutation: true,
        },
      );
      onRefresh();
    } catch (requestError) {
      setError(toApiError(requestError));
    } finally {
      setSimulating(false);
    }
  }

  return (
    <article className="job-card">
      <div className="job-card-head">
        <div>
          <p className="data-label">Demo job · {job.stable_key}</p>
          <h3>{job.name}</h3>
        </div>
        <span className={`job-status job-${job.status}`}>{job.status}</span>
      </div>
      <dl className="job-facts">
        <div>
          <dt>Retries</dt>
          <dd>{job.retry_count}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>v{job.version}</dd>
        </div>
        <div>
          <dt>Last update</dt>
          <dd>
            <Timestamp value={job.updated_at} />
          </dd>
        </div>
      </dl>
      {job.last_error ? <p className="job-error">{job.last_error}</p> : null}
      {job.status === "failed" ? (
        <RetryJobForm
          incident={incident}
          job={job}
          onAction={onAction}
          onRefresh={onRefresh}
        />
      ) : null}
      {demoToolsEnabled && job.status !== "failed" ? (
        <button
          className="button button-quiet"
          disabled={simulating}
          onClick={simulateFailure}
          type="button"
        >
          {simulating ? "Simulating…" : "Simulate demo failure"}
        </button>
      ) : null}
      {demoToolsEnabled && job.status === "failed" ? (
        <button
          className="button button-quiet"
          disabled={simulating}
          onClick={simulateFailure}
          type="button"
        >
          {simulating ? "Simulating…" : "Simulate demo failure"}
        </button>
      ) : null}
      {error ? (
        <ErrorPanel error={error} title="The demo job was not changed" />
      ) : null}
    </article>
  );
}

export function IncidentDetailScreen({
  incidentId,
  demoToolsEnabled = false,
}: {
  incidentId: string;
  demoToolsEnabled?: boolean;
}) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [events, setEvents] = useState<TimelinePage["items"]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [reloadVersion, setReloadVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      requestJson<Incident>(`/api/ui/incidents/${incidentId}`, {
        signal: controller.signal,
      }),
      requestJson<TimelinePage>(
        `/api/ui/incidents/${incidentId}/timeline?limit=20`,
        { signal: controller.signal },
      ),
    ])
      .then(([incidentResult, timeline]) => {
        setIncident(incidentResult);
        setEvents(timeline.items);
        setNextCursor(timeline.next_cursor);
        setError(null);
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) setError(toApiError(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [incidentId, reloadVersion]);

  function refresh() {
    setReloadVersion((version) => version + 1);
  }

  async function saveIncident(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!incident) return;
    const form = new FormData(event.currentTarget);
    setSaveError(null);
    setAnnouncement("");
    try {
      const updated = await requestJson<Incident>(
        `/api/ui/incidents/${incident.id}`,
        {
          method: "PATCH",
          mutation: true,
          body: {
            expected_version: incident.version,
            title: String(form.get("title") ?? ""),
            description: String(form.get("description") ?? ""),
            service: String(form.get("service") ?? ""),
            severity: String(form.get("severity") ?? ""),
            status: String(form.get("status") ?? "") as IncidentStatus,
          },
        },
      );
      setIncident(updated);
      setAnnouncement(`Incident saved at version ${updated.version}.`);
      refresh();
    } catch (requestError) {
      setSaveError(toApiError(requestError));
    }
  }

  async function loadMoreTimeline() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await requestJson<TimelinePage>(
        `/api/ui/incidents/${incidentId}/timeline?cursor=${encodeURIComponent(nextCursor)}&limit=20`,
      );
      setEvents((current) => [
        ...new Map(
          [...current, ...page.items].map((item) => [item.id, item]),
        ).values(),
      ]);
      setNextCursor(page.next_cursor);
    } catch (requestError) {
      setError(toApiError(requestError));
    } finally {
      setLoadingMore(false);
    }
  }

  if (loading) {
    return (
      <div className="page-stack">
        <LoadingLedger label="Loading incident" />
      </div>
    );
  }
  if (error || !incident) {
    return (
      <div className="page-stack">
        <ErrorPanel
          error={
            error ?? {
              code: "not_found",
              message: "The incident could not be found.",
              status: 404,
            }
          }
          onRetry={() => {
            setLoading(true);
            refresh();
          }}
        />
      </div>
    );
  }

  return (
    <div className="page-stack">
      <nav aria-label="Breadcrumb" className="breadcrumbs">
        <Link href="/incidents">Incidents</Link>
        <span aria-hidden="true">/</span>
        <span>{incident.id.slice(0, 8)}</span>
      </nav>

      <header className={`incident-hero severity-rail-${incident.severity}`}>
        <div className="incident-hero-main">
          <p className="eyebrow">
            {incident.service} · {incident.id.slice(0, 8)}
          </p>
          <h1>{incident.title}</h1>
          <p>{incident.description}</p>
        </div>
        <div className="incident-hero-state">
          <SeverityBadge severity={incident.severity} />
          <StatusBadge status={incident.status} />
          <span className="hero-version">
            version {incident.version}{" "}
            <VersionBadge version={incident.version} />
          </span>
          <Timestamp exact value={incident.updated_at} />
        </div>
      </header>

      {action ? (
        <section className="action-result" role="status">
          <ActionStatusBadge status={action.status} />
          <p>{actionMessage(action)}</p>
          <Link href={`/approvals/${action.id}`}>Review action</Link>
        </section>
      ) : null}
      {announcement ? (
        <p className="success-line" role="status">
          {announcement}
        </p>
      ) : null}

      <div className="detail-grid">
        <section aria-labelledby="jobs-heading" className="ledger-section">
          <div className="section-heading">
            <span>01</span>
            <div>
              <p className="eyebrow">Remediation surface</p>
              <h2 id="jobs-heading">Demo jobs</h2>
            </div>
          </div>
          {incident.jobs?.length ? (
            <div className="job-list">
              {incident.jobs.map((job) => (
                <DemoJobCard
                  demoToolsEnabled={demoToolsEnabled}
                  incident={incident}
                  job={job}
                  key={job.id}
                  onAction={setAction}
                  onRefresh={refresh}
                />
              ))}
            </div>
          ) : (
            <div className="section-empty">
              <h3>No demo jobs attached</h3>
              <p>This incident has no bounded remediation fixture.</p>
            </div>
          )}
        </section>

        <section
          aria-labelledby="edit-heading"
          className="ledger-section edit-section"
        >
          <div className="section-heading">
            <span>02</span>
            <div>
              <p className="eyebrow">Authoritative update</p>
              <h2 id="edit-heading">Edit incident</h2>
            </div>
          </div>
          {saveError ? (
            <ErrorPanel error={saveError} title="The incident was not saved" />
          ) : null}
          <form className="work-form compact-form" onSubmit={saveIncident}>
            <label className="field">
              <span>Title</span>
              <input
                aria-label="Title"
                defaultValue={incident.title}
                maxLength={200}
                name="title"
                required
              />
            </label>
            <label className="field">
              <span>Description</span>
              <textarea
                aria-label="Description"
                defaultValue={incident.description}
                maxLength={10_000}
                name="description"
                required
                rows={6}
              />
            </label>
            <div className="form-grid">
              <label className="field">
                <span>Service</span>
                <input
                  aria-label="Service"
                  defaultValue={incident.service}
                  maxLength={100}
                  name="service"
                  required
                />
              </label>
              <label className="field">
                <span>Severity</span>
                <select
                  aria-label="Severity"
                  defaultValue={incident.severity}
                  name="severity"
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </label>
              <label className="field field-wide">
                <span>Status</span>
                <select
                  aria-label="Status"
                  defaultValue={incident.status}
                  name="status"
                >
                  <option value="open">Open</option>
                  <option value="investigating">Investigating</option>
                  <option value="resolved">Resolved</option>
                  <option value="closed">Closed</option>
                </select>
              </label>
            </div>
            <p className="field-hint">
              The backend validates lifecycle and authorization rules. This edit
              will be checked against version {incident.version}.
            </p>
            <button className="button button-primary" type="submit">
              Save incident
            </button>
          </form>
        </section>
      </div>

      <section
        aria-labelledby="timeline-heading"
        className="ledger-section timeline-section"
      >
        <div className="section-heading">
          <span>03</span>
          <div>
            <p className="eyebrow">Immutable record</p>
            <h2 id="timeline-heading">Audit log</h2>
          </div>
        </div>
        <Timeline events={events} />
        {nextCursor ? (
          <button
            className="button button-secondary"
            disabled={loadingMore}
            onClick={loadMoreTimeline}
            type="button"
          >
            {loadingMore ? "Loading…" : "Load older events"}
          </button>
        ) : null}
      </section>
    </div>
  );
}

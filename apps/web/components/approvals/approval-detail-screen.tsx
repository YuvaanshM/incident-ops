"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  actionLabel,
  humanize,
} from "@/components/approvals/approval-queue-screen";
import { ActionStatusBadge, SeverityBadge } from "@/components/ui/badges";
import { ErrorPanel, LoadingLedger } from "@/components/ui/error-panel";
import { Timestamp } from "@/components/ui/timestamp";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type { Action, Incident } from "@/lib/api/types";

function displayValue(value: unknown): string {
  if (typeof value === "string") return humanize(value);
  if (value === null) return "None";
  return JSON.stringify(value);
}

function FactList({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="decision-facts">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}>
          <dt>{humanize(key)}</dt>
          <dd>{displayValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ApprovalDetailScreen({ actionId }: { actionId: string }) {
  const [action, setAction] = useState<Action | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [loading, setLoading] = useState(true);
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [decisionError, setDecisionError] = useState<ApiError | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [rejecting, setRejecting] = useState(false);

  const refreshAction = useCallback(async () => {
    const latest = await requestJson<Action>(`/api/ui/actions/${actionId}`);
    setAction(latest);
    return latest;
  }, [actionId]);

  useEffect(() => {
    const controller = new AbortController();
    void requestJson<Action>(`/api/ui/actions/${actionId}`, {
      signal: controller.signal,
    })
      .then(async (actionResult) => {
        setAction(actionResult);
        const incidentResult = await requestJson<Incident>(
          `/api/ui/incidents/${actionResult.incident_id}`,
          { signal: controller.signal },
        );
        setIncident(incidentResult);
        setError(null);
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) setError(toApiError(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [actionId]);

  useEffect(() => {
    if (action?.status !== "pending") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !deciding) {
        void refreshAction().catch(() => undefined);
      }
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [action?.status, deciding, refreshAction]);

  async function decide(kind: "approve" | "reject", reason?: string) {
    setDeciding(kind);
    setDecisionError(null);
    setAnnouncement("");
    try {
      const body =
        kind === "reject" ? { reason: reason?.trim() || null } : undefined;
      const result = await requestJson<Action>(
        `/api/ui/actions/${actionId}/${kind}`,
        { method: "POST", mutation: true, body },
      );
      setAction(result);
      setRejecting(false);
      setAnnouncement(`This request was ${result.status}.`);
    } catch (requestError) {
      const apiError = toApiError(requestError);
      if (apiError.code === "action_not_pending") {
        setAnnouncement(
          "Another operator already decided this request. The latest result is shown below.",
        );
        try {
          await refreshAction();
        } catch (refreshError) {
          setDecisionError(toApiError(refreshError));
        }
      } else {
        setDecisionError(apiError);
      }
    } finally {
      setDeciding(null);
    }
  }

  if (loading) {
    return (
      <div className="page-stack page-narrow">
        <LoadingLedger label="Loading approval request" />
      </div>
    );
  }
  if (error || !action || !incident) {
    return (
      <div className="page-stack page-narrow">
        <ErrorPanel
          error={
            error ?? {
              code: "not_found",
              message: "The approval request could not be found.",
              status: 404,
            }
          }
          {...(error?.status === 403
            ? { title: "Approval access required" }
            : {})}
        />
      </div>
    );
  }

  return (
    <div className="page-stack page-narrow">
      <nav aria-label="Breadcrumb" className="breadcrumbs">
        <Link href="/approvals">Approvals</Link>
        <span aria-hidden="true">/</span>
        <span>{action.id.slice(0, 8)}</span>
      </nav>

      <header className="decision-header">
        <div>
          <p className="eyebrow">Bounded action request</p>
          <h1>{actionLabel(action.action_type)}</h1>
          <p>
            {action.rationale || "No rationale was supplied with this request."}
          </p>
        </div>
        <div className="decision-status">
          <ActionStatusBadge status={action.status} />
          <Timestamp exact value={action.created_at} />
        </div>
      </header>

      {announcement ? (
        <p className="decision-announcement" role="status">
          {announcement}
        </p>
      ) : null}
      {decisionError ? (
        <ErrorPanel
          error={decisionError}
          title="The decision was not recorded"
        />
      ) : null}

      <section aria-labelledby="incident-context" className="decision-section">
        <div className="section-heading">
          <span>01</span>
          <div>
            <p className="eyebrow">Affected record</p>
            <h2 id="incident-context">Incident context</h2>
          </div>
        </div>
        <article
          className={`decision-incident severity-rail-${incident.severity}`}
        >
          <div>
            <Link href={`/incidents/${incident.id}`}>{incident.title}</Link>
            <p>{incident.description}</p>
          </div>
          <div className="decision-incident-state">
            <SeverityBadge severity={incident.severity} />
            <span>{incident.service}</span>
            <span>version {incident.version}</span>
          </div>
        </article>
        {incident.monitoring_status ? (
          <p>
            Monitoring: {humanize(incident.monitoring_status.toLowerCase())}.
            Source recovery does not approve this request or execute
            remediation. Approval rechecks current evidence before applying the
            action.
          </p>
        ) : null}
      </section>

      <div className="decision-grid">
        <section
          aria-labelledby="parameters-heading"
          className="decision-section"
        >
          <div className="section-heading compact-heading">
            <span>02</span>
            <div>
              <p className="eyebrow">Requested operation</p>
              <h2 id="parameters-heading">Parameters</h2>
            </div>
          </div>
          <FactList values={action.parameters} />
        </section>
        <section aria-labelledby="policy-heading" className="decision-section">
          <div className="section-heading compact-heading">
            <span>03</span>
            <div>
              <p className="eyebrow">Decision boundary</p>
              <h2 id="policy-heading">Policy snapshot</h2>
            </div>
          </div>
          <FactList values={action.policy_snapshot} />
          <p className="policy-version">Policy {action.policy_version}</p>
        </section>
      </div>

      {action.status === "pending" ? (
        <section
          aria-labelledby="decision-heading"
          className="decision-console"
        >
          <div>
            <p className="eyebrow">Human checkpoint</p>
            <h2 id="decision-heading">Record a decision</h2>
            <p>
              Approval authorizes only the operation and parameters shown above.
            </p>
          </div>
          {rejecting ? (
            <form
              className="reject-form"
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                void decide("reject", String(form.get("reason") ?? ""));
              }}
            >
              <label className="field">
                <span>Rejection reason</span>
                <textarea
                  autoFocus
                  maxLength={1000}
                  name="reason"
                  placeholder="Explain why this operation should not run."
                  rows={3}
                />
              </label>
              <div className="decision-buttons">
                <button
                  className="button button-danger"
                  disabled={deciding !== null}
                  type="submit"
                >
                  {deciding === "reject" ? "Rejecting…" : "Confirm rejection"}
                </button>
                <button
                  className="button button-quiet"
                  disabled={deciding !== null}
                  onClick={() => setRejecting(false)}
                  type="button"
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <div className="decision-buttons">
              <button
                className="button button-approve"
                disabled={deciding !== null}
                onClick={() => void decide("approve")}
                type="button"
              >
                {deciding === "approve" ? "Approving…" : "Approve request"}
              </button>
              <button
                className="button button-danger"
                disabled={deciding !== null}
                onClick={() => setRejecting(true)}
                type="button"
              >
                Reject request
              </button>
            </div>
          )}
        </section>
      ) : (
        <section className="decision-complete">
          <p className="notice-kicker">Decision recorded</p>
          <h2>{humanize(action.status)}</h2>
          {action.decided_by_id ? (
            <p>
              Decided by {action.decided_by_id}
              {action.decided_at ? (
                <>
                  {" "}
                  on <Timestamp exact value={action.decided_at} />
                </>
              ) : null}
            </p>
          ) : null}
          {action.outcome_code ? (
            <p>Outcome: {humanize(action.outcome_code)}</p>
          ) : null}
          {action.status === "stale" ? (
            <p>
              The evidence changed before approval. Nothing was executed.{" "}
              <Link href={`/incidents/${incident.id}`}>
                Review the latest incident
              </Link>{" "}
              before submitting a new request.
            </p>
          ) : null}
        </section>
      )}
    </div>
  );
}

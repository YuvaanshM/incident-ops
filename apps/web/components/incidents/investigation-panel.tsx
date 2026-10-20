"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ErrorPanel } from "@/components/ui/error-panel";
import { requestJson, toApiError } from "@/lib/api/browser";
import type { ApiError } from "@/lib/api/errors";
import type { InvestigationResult } from "@/lib/api/types";
import { UUID_PATTERN } from "@/lib/webmcp/contracts";

type Props = { incidentId: string; incidentVersion: number };

export function InvestigationPanel(props: Props) {
  return <Investigation key={props.incidentId} {...props} />;
}

function Investigation({ incidentId, incidentVersion }: Props) {
  const [allowActions, setAllowActions] = useState(false);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [key, setKey] = useState("");
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const findings = result?.outcome.findings ?? [];
  useEffect(() => () => controller.current?.abort(), []);

  async function run(recover: boolean) {
    const current = new AbortController();
    controller.current = current;
    const requestKey = recover ? key : crypto.randomUUID();
    setKey(requestKey);
    setError(null);
    setStopped(false);
    setLoading(true);
    setActionStatus(null);
    if (!recover) setResult(null);
    try {
      const response = await requestJson<InvestigationResult>(
        recover
          ? `/api/agent-investigations/recover/${requestKey}`
          : `/api/agent-investigations/${incidentId}`,
        {
          method: "POST",
          signal: current.signal,
          headers: {
            "X-Incident-Ops-Intent": "agent-tool",
            "Idempotency-Key": requestKey,
          },
          body: recover
            ? {}
            : {
                expected_incident_version: incidentVersion,
                allow_action_requests: allowActions,
              },
        },
      );
      if (!current.signal.aborted) setResult(response);
    } catch (cause) {
      if (!current.signal.aborted) setError(toApiError(cause));
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }

  async function checkAction() {
    if (!result) return;
    setError(null);
    try {
      const action = await requestJson<{ status: string }>(
        `/api/agent-tools/requests/${result.action_request_key}`,
        {
          method: "POST",
          headers: { "X-Incident-Ops-Intent": "agent-tool" },
          body: {},
        },
      );
      setActionStatus(action.status);
    } catch (cause) {
      setError(toApiError(cause));
    }
  }

  return (
    <section
      className="ledger-section precedent-section"
      aria-labelledby="investigation-heading"
    >
      <div className="section-heading">
        <span>Optional</span>
        <div>
          <p className="eyebrow">Bounded agent · synthetic demo only</p>
          <h2 id="investigation-heading">Investigate with AI</h2>
        </div>
      </div>
      <p>
        Gather incident, alert and historical evidence into a cited draft.
        Availability requires an operator-configured model. Each run stops
        within 40 seconds and reserves at most $0.10.
      </p>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={allowActions}
          disabled={loading}
          onChange={(event) => setAllowActions(event.target.checked)}
        />{" "}
        Allow one remediation request under current policy
      </label>
      <p>
        Requests may apply automatically or require human approval. The
        investigator cannot approve or close incidents.
      </p>
      <div className="form-actions">
        <button
          className="button button-secondary"
          type="button"
          disabled={loading}
          onClick={() => void run(false)}
        >
          Start investigation
        </button>
        {loading ? (
          <button
            className="button button-secondary"
            type="button"
            onClick={() => {
              controller.current?.abort();
              setLoading(false);
              setStopped(true);
            }}
          >
            Stop investigation
          </button>
        ) : null}
      </div>
      {stopped ? (
        <p role="status">
          Stopped waiting. Stopping does not undo an action already submitted.
          Check the investigation and any action request before starting again.
        </p>
      ) : null}
      {
        <div>
          <label>
            Recovery key{" "}
            <input
              aria-label="Investigation recovery key"
              value={key}
              disabled={loading}
              onChange={(event) => setKey(event.target.value)}
              maxLength={36}
            />
          </label>
          <button
            className="button button-secondary"
            type="button"
            disabled={loading || !UUID_PATTERN.test(key)}
            onClick={() => void run(true)}
          >
            Check investigation
          </button>
        </div>
      }
      {error ? (
        <ErrorPanel error={error} title="Investigation unavailable" />
      ) : null}
      <div aria-live="polite" aria-busy={loading}>
        {loading ? <p>Gathering evidence…</p> : null}
        {result ? (
          <>
            <p>
              Investigation {result.outcome.status}
              {result.outcome.reason
                ? ` · ${result.outcome.reason.replaceAll("_", " ")}`
                : ""}
            </p>
            <p className="data-label">
              {result.model} · incident version {result.incident_version} ·{" "}
              {result.outcome.input_tokens + result.outcome.output_tokens}{" "}
              reported tokens · $
              {(result.outcome.reserved_cost_microusd / 1_000_000).toFixed(4)}{" "}
              reserved ceiling
            </p>
            {result.incident_version !== incidentVersion ? (
              <p>
                This draft refers to an earlier incident version. Review the
                current incident before taking action.
              </p>
            ) : null}
            {findings.length ? (
              <>
                <h3>Quoted observations</h3>
                <p>
                  Untrusted source text selected by the model. These quotes
                  establish attribution, not cause or approval.
                </p>
                <ul>
                  {findings.map((finding, index) => {
                    const [kind, sourceId, sourceVersion] =
                      finding.source_id.split(":");
                    const href =
                      kind === "source" &&
                      sourceId &&
                      UUID_PATTERN.test(sourceId)
                        ? `/historical-incidents/${sourceId}${sourceVersion && /^v[1-9][0-9]{0,18}$/.test(sourceVersion) ? `?version=${sourceVersion.slice(1)}` : ""}`
                        : `/incidents/${result.incident_id}`;
                    return (
                      <li key={`${finding.source_id}-${index}`}>
                        <blockquote>{finding.quote}</blockquote>
                        <Link href={href}>{finding.source_id}</Link>
                      </li>
                    );
                  })}
                </ul>
              </>
            ) : null}
            {result.outcome.action ? (
              <p>
                Action request:{" "}
                {String(result.outcome.action.status ?? "check result")}. Review
                its policy outcome in the incident timeline.
              </p>
            ) : null}
            <button
              type="button"
              className="button button-secondary"
              onClick={() => void checkAction()}
            >
              Check action request
            </button>
            {actionStatus ? (
              <p>Recovered action status: {actionStatus}</p>
            ) : null}
            <details>
              <summary>Investigation trace</summary>
              <ol>
                {(result.outcome.trace ?? []).map((step, index) => (
                  <li key={index}>
                    {step.tool} · {step.status}
                  </li>
                ))}
              </ol>
              <p>Run {result.id}</p>
            </details>
          </>
        ) : null}
      </div>
    </section>
  );
}

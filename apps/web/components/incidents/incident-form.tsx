"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { ErrorPanel } from "@/components/ui/error-panel";
import { requestJson, toApiError } from "@/lib/api/browser";
import { fieldErrors, type ApiError } from "@/lib/api/errors";
import type { Incident, IncidentCreate } from "@/lib/api/types";

interface IncidentFormProps {
  onCreated?: (incident: Incident) => void;
}

export function IncidentForm({ onCreated }: IncidentFormProps) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [service, setService] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] =
    useState<IncidentCreate["severity"]>("medium");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [created, setCreated] = useState(false);
  const fields = fieldErrors(error);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setCreated(false);
    setError(null);
    try {
      const result = await requestJson<Incident>("/api/ui/incidents", {
        method: "POST",
        mutation: true,
        body: {
          title,
          service,
          description,
          severity,
        } satisfies IncidentCreate,
      });
      setCreated(true);
      if (onCreated) {
        onCreated(result);
      } else {
        router.push(`/incidents/${result.id}`);
      }
    } catch (requestError) {
      setError(toApiError(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="work-form" onSubmit={submit}>
      {error ? (
        <ErrorPanel error={error} title="The incident was not created" />
      ) : null}
      {created ? (
        <p role="status">Incident created. Opening its ledger…</p>
      ) : null}

      <div className="form-grid">
        <label className="field field-wide">
          <span>Incident title</span>
          <input
            aria-label="Incident title"
            aria-describedby={fields.title ? "title-error" : undefined}
            aria-invalid={Boolean(fields.title)}
            autoComplete="off"
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
            required
            value={title}
          />
          {fields.title ? (
            <span className="field-error" id="title-error">
              {fields.title}
            </span>
          ) : null}
        </label>

        <label className="field">
          <span>Affected service</span>
          <input
            aria-label="Affected service"
            aria-describedby={fields.service ? "service-error" : undefined}
            aria-invalid={Boolean(fields.service)}
            autoComplete="off"
            maxLength={100}
            onChange={(event) => setService(event.target.value)}
            required
            value={service}
          />
          {fields.service ? (
            <span className="field-error" id="service-error">
              {fields.service}
            </span>
          ) : null}
        </label>

        <label className="field">
          <span>Severity</span>
          <select
            aria-label="Severity"
            onChange={(event) =>
              setSeverity(event.target.value as IncidentCreate["severity"])
            }
            value={severity}
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </label>

        <label className="field field-wide">
          <span>What is happening?</span>
          <textarea
            aria-label="What is happening?"
            aria-describedby={
              fields.description ? "description-error" : "description-hint"
            }
            aria-invalid={Boolean(fields.description)}
            maxLength={10_000}
            onChange={(event) => setDescription(event.target.value)}
            required
            rows={8}
            value={description}
          />
          {fields.description ? (
            <span className="field-error" id="description-error">
              {fields.description}
            </span>
          ) : (
            <span className="field-hint" id="description-hint">
              Describe impact and evidence. Do not paste credentials or customer
              secrets.
            </span>
          )}
        </label>
      </div>

      <div className="form-actions">
        <button
          className="button button-primary"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "Creating…" : "Create incident"}
        </button>
        <Link className="button button-quiet" href="/incidents">
          Cancel
        </Link>
      </div>
    </form>
  );
}

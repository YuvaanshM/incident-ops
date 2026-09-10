import type { components } from "@incident-ops/api-client";

export type Incident = components["schemas"]["IncidentResponse"];
export type IncidentEvent = components["schemas"]["IncidentEventResponse"];
export type Action = components["schemas"]["ActionResponse"];

export const incident: Incident = {
  id: "10000000-0000-4000-8000-000000000001",
  title: "Checkout latency above SLO",
  description: "Customers are waiting more than five seconds at checkout.",
  service: "checkout",
  severity: "high",
  status: "investigating",
  version: 4,
  created_by_type: "human",
  created_by_id: "operator@example.com",
  created_at: "2026-09-09T12:00:00Z",
  updated_at: "2026-09-09T12:42:00Z",
  resolved_at: null,
  closed_at: null,
  jobs: [],
};

export const timelineEvent: IncidentEvent = {
  id: "50000000-0000-4000-8000-000000000001",
  incident_id: incident.id,
  action_request_id: null,
  event_type: "incident.updated",
  actor_type: "human",
  actor_id: "operator@example.com",
  request_id: "60000000-0000-4000-8000-000000000001",
  payload: {
    changes: {
      status: { from: "open", to: "investigating" },
      description: "Customer-facing checkout delay",
    },
  },
  occurred_at: "2026-09-09T12:42:00Z",
};

export const incidentWithJob: Incident = {
  ...incident,
  jobs: [
    {
      id: "20000000-0000-4000-8000-000000000001",
      incident_id: incident.id,
      stable_key: "checkout-reindex",
      name: "Rebuild checkout search index",
      status: "failed",
      retry_count: 1,
      version: 2,
      last_error: "Synthetic timeout",
      created_at: "2026-09-09T12:05:00Z",
      updated_at: "2026-09-09T12:30:00Z",
    },
  ],
};

export const pendingAction: Action = {
  id: "30000000-0000-4000-8000-000000000001",
  incident_id: incident.id,
  action_type: "retry_failed_job",
  status: "pending",
  execution_mode: "human",
  requested_by_type: "human",
  requested_by_id: "operator@example.com",
  idempotency_key: "40000000-0000-4000-8000-000000000001",
  base_incident_version: 4,
  parameters: { job_id: incidentWithJob.jobs?.[0]?.id },
  policy_version: "2026-09-05.v1",
  policy_snapshot: { decision: "require_approval" },
  rationale: "The first automated retry did not recover checkout.",
  outcome_code: null,
  outcome_payload: null,
  decided_by_type: null,
  decided_by_id: null,
  decided_at: null,
  created_at: "2026-09-09T12:43:00Z",
  updated_at: "2026-09-09T12:43:00Z",
};

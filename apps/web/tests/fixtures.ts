import type { components } from "@incident-ops/api-client";

export type Incident = components["schemas"]["IncidentResponse"];
export type IncidentEvent = components["schemas"]["IncidentEventResponse"];

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

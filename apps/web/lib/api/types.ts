import type { components } from "@incident-ops/api-client";

export type Incident = components["schemas"]["IncidentResponse"];
export type IncidentCreate = components["schemas"]["IncidentCreate"];
export type IncidentUpdate = components["schemas"]["IncidentUpdate"];
export type IncidentPage = components["schemas"]["IncidentListResponse"];
export type IncidentSeverity = components["schemas"]["IncidentSeverity"];
export type IncidentStatus = components["schemas"]["IncidentStatus"];
export type IncidentEvent = components["schemas"]["IncidentEventResponse"];
export type TimelinePage = components["schemas"]["TimelineResponse"];
export type DemoJob = components["schemas"]["DemoJobResponse"];
export type Action = components["schemas"]["ActionResponse"];
export type ActionPage = components["schemas"]["ActionListResponse"];
export type ActionStatus = components["schemas"]["ActionStatus"];

export interface IncidentFilters {
  q?: string;
  service?: string;
  severity?: IncidentSeverity;
  status?: IncidentStatus;
}

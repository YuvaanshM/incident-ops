import type { Metadata } from "next";

import { IncidentListScreen } from "@/components/incidents/incident-list-screen";
import type {
  IncidentFilters,
  IncidentSeverity,
  IncidentStatus,
} from "@/lib/api/types";

export const metadata: Metadata = { title: "Incidents" };

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

const severities = new Set<IncidentSeverity>([
  "low",
  "medium",
  "high",
  "critical",
]);
const statuses = new Set<IncidentStatus>([
  "open",
  "investigating",
  "resolved",
  "closed",
]);

export default async function IncidentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const severityValue = first(query.severity);
  const statusValue = first(query.status);
  const filters: IncidentFilters = {};
  const q = first(query.q)?.trim();
  const service = first(query.service)?.trim();
  if (q) filters.q = q;
  if (service) filters.service = service;
  if (severityValue && severities.has(severityValue as IncidentSeverity)) {
    filters.severity = severityValue as IncidentSeverity;
  }
  if (statusValue && statuses.has(statusValue as IncidentStatus)) {
    filters.status = statusValue as IncidentStatus;
  }
  return <IncidentListScreen initialFilters={filters} />;
}

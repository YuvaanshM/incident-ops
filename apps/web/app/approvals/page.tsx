import type { Metadata } from "next";

import { ApprovalQueueScreen } from "@/components/approvals/approval-queue-screen";
import type { ActionStatus } from "@/lib/api/types";

export const metadata: Metadata = { title: "Approvals" };

const actionStatuses = new Set<ActionStatus>([
  "pending",
  "applied",
  "rejected",
  "failed",
  "stale",
]);
const ages = new Set([15, 60, 240, 1440, 10_080]);
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const rawStatus = first(query.status);
  const status =
    rawStatus === undefined
      ? "pending"
      : rawStatus && actionStatuses.has(rawStatus as ActionStatus)
        ? (rawStatus as ActionStatus)
        : "";
  const rawIncidentId = first(query.incident_id)?.trim() ?? "";
  const incidentId = uuidPattern.test(rawIncidentId) ? rawIncidentId : "";
  const rawAge = Number(first(query.minimum_age_minutes));
  const minimumAgeMinutes = ages.has(rawAge) ? rawAge : null;
  return (
    <ApprovalQueueScreen
      initialIncidentId={incidentId}
      initialMinimumAgeMinutes={minimumAgeMinutes}
      initialStatus={status}
    />
  );
}

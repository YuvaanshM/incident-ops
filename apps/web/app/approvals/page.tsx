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

export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const rawStatus = Array.isArray(query.status)
    ? query.status[0]
    : query.status;
  const status =
    rawStatus && actionStatuses.has(rawStatus as ActionStatus)
      ? (rawStatus as ActionStatus)
      : "";
  return <ApprovalQueueScreen initialStatus={status} />;
}

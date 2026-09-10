import type { Metadata } from "next";

import { ApprovalDetailScreen } from "@/components/approvals/approval-detail-screen";

export const metadata: Metadata = { title: "Approval request" };

export default async function ApprovalPage({
  params,
}: {
  params: Promise<{ actionId: string }>;
}) {
  const { actionId } = await params;
  return <ApprovalDetailScreen actionId={actionId} />;
}

import type { Metadata } from "next";

import { IncidentDetailScreen } from "@/components/incidents/incident-detail-screen";

export const metadata: Metadata = { title: "Incident detail" };

export default async function IncidentDetailPage({
  params,
}: {
  params: Promise<{ incidentId: string }>;
}) {
  const { incidentId } = await params;
  return (
    <IncidentDetailScreen
      demoToolsEnabled={process.env.NODE_ENV !== "production"}
      incidentId={incidentId}
    />
  );
}

import type { Metadata } from "next";
import { HistoricalSourceScreen } from "@/components/incidents/historical-source";
export const metadata: Metadata = { title: "Historical source" };
export default async function HistoricalSourcePage({
  params,
  searchParams,
}: {
  params: Promise<{ sourceId: string }>;
  searchParams: Promise<{ version?: string | string[] }>;
}) {
  const { sourceId } = await params;
  const { version } = await searchParams;
  return (
    <HistoricalSourceScreen
      key={sourceId}
      sourceId={sourceId}
      citedVersion={typeof version === "string" ? version : undefined}
    />
  );
}

import {
  forwardAdvisoryQuery,
  invalidIdentifier,
  isUuid,
} from "@/lib/api/server";
export const dynamic = "force-dynamic";
export async function POST(
  request: Request,
  context: { params: Promise<{ incidentId: string }> },
) {
  const { incidentId } = await context.params;
  if (!isUuid(incidentId)) return invalidIdentifier();
  return forwardAdvisoryQuery(
    request,
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/remediation-suggestions`,
  );
}

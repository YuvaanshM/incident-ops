import { forwardQuery, invalidIdentifier, isUuid } from "@/lib/api/server";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ incidentId: string }> },
) {
  const { incidentId } = await context.params;
  if (!isUuid(incidentId)) return invalidIdentifier();
  return forwardQuery(
    request,
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/alerts`,
  );
}

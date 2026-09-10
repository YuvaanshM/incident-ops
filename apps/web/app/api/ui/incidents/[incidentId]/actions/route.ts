import { forwardMutation, invalidIdentifier, isUuid } from "@/lib/api/server";

export async function POST(
  request: Request,
  context: { params: Promise<{ incidentId: string }> },
) {
  const { incidentId } = await context.params;
  if (!isUuid(incidentId)) return invalidIdentifier();
  return forwardMutation(
    request,
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/actions`,
    "POST",
  );
}

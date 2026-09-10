import {
  forwardMutation,
  forwardQuery,
  invalidIdentifier,
  isUuid,
} from "@/lib/api/server";

export const dynamic = "force-dynamic";

interface Context {
  params: Promise<{ incidentId: string }>;
}

export async function GET(request: Request, context: Context) {
  const { incidentId } = await context.params;
  if (!isUuid(incidentId)) return invalidIdentifier();
  return forwardQuery(
    request,
    `/api/v1/incidents/${encodeURIComponent(incidentId)}`,
  );
}

export async function PATCH(request: Request, context: Context) {
  const { incidentId } = await context.params;
  if (!isUuid(incidentId)) return invalidIdentifier();
  return forwardMutation(
    request,
    `/api/v1/incidents/${encodeURIComponent(incidentId)}`,
    "PATCH",
  );
}

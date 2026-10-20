import { forwardInvestigation } from "@/lib/agent-tools/server";

export async function POST(
  request: Request,
  context: { params: Promise<{ incidentId: string }> },
) {
  const { incidentId } = await context.params;
  return forwardInvestigation(request, incidentId);
}

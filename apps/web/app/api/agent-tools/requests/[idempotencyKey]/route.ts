import { recoverAgentRequest } from "@/lib/agent-tools/server";

export async function POST(
  request: Request,
  context: { params: Promise<{ idempotencyKey: string }> },
) {
  const { idempotencyKey } = await context.params;
  return recoverAgentRequest(request, idempotencyKey);
}

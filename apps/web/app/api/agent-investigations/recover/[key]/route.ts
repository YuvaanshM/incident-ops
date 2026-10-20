import { recoverInvestigation } from "@/lib/agent-tools/server";

export async function POST(
  request: Request,
  context: { params: Promise<{ key: string }> },
) {
  const { key } = await context.params;
  return recoverInvestigation(request, key);
}

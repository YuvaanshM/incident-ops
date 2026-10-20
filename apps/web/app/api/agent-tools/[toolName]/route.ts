import { forwardAgentTool } from "@/lib/agent-tools/server";

export async function POST(
  request: Request,
  context: { params: Promise<{ toolName: string }> },
) {
  const { toolName } = await context.params;
  return forwardAgentTool(request, toolName);
}

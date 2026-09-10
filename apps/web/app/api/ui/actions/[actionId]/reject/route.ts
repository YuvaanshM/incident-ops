import { forwardMutation, invalidIdentifier, isUuid } from "@/lib/api/server";

interface Context {
  params: Promise<{ actionId: string }>;
}

export async function POST(request: Request, context: Context) {
  const { actionId } = await context.params;
  if (!isUuid(actionId)) return invalidIdentifier();
  return forwardMutation(
    request,
    `/api/v1/actions/${encodeURIComponent(actionId)}/reject`,
    "POST",
  );
}

import { forwardQuery, invalidIdentifier, isUuid } from "@/lib/api/server";

export const dynamic = "force-dynamic";

interface Context {
  params: Promise<{ actionId: string }>;
}

export async function GET(request: Request, context: Context) {
  const { actionId } = await context.params;
  if (!isUuid(actionId)) return invalidIdentifier();
  return forwardQuery(
    request,
    `/api/v1/actions/${encodeURIComponent(actionId)}`,
  );
}

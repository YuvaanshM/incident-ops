import { forwardQuery, invalidIdentifier, isUuid } from "@/lib/api/server";
export const dynamic = "force-dynamic";
export async function GET(
  request: Request,
  context: { params: Promise<{ sourceId: string }> },
) {
  const { sourceId } = await context.params;
  if (!isUuid(sourceId)) return invalidIdentifier();
  return forwardQuery(
    request,
    `/api/v1/historical-incidents/${encodeURIComponent(sourceId)}`,
  );
}

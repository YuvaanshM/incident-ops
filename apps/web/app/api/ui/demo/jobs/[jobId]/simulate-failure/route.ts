import { forwardMutation, invalidIdentifier, isUuid } from "@/lib/api/server";

export async function POST(
  request: Request,
  context: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await context.params;
  if (!isUuid(jobId)) return invalidIdentifier();
  return forwardMutation(
    request,
    `/api/v1/demo/jobs/${encodeURIComponent(jobId)}/simulate-failure`,
    "POST",
  );
}

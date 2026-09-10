import { forwardQuery } from "@/lib/api/server";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  return forwardQuery(request, "/api/v1/actions");
}

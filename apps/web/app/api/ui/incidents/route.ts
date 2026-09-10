import { forwardMutation, forwardQuery } from "@/lib/api/server";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  return forwardQuery(request, "/api/v1/incidents");
}

export function POST(request: Request) {
  return forwardMutation(request, "/api/v1/incidents", "POST");
}

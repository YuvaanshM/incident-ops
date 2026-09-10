import "server-only";

import { auth0 } from "@/lib/auth0";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_MUTATION_BYTES = 24_000;

function apiBaseUrl(): string {
  return (
    process.env.API_INTERNAL_URL ??
    process.env.API_PUBLIC_URL ??
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
}

async function humanAccessToken(): Promise<
  { token: string } | { error: Response }
> {
  const configured = process.env.INCIDENT_OPS_HUMAN_ACCESS_TOKEN?.trim();
  if (process.env.NODE_ENV !== "production") {
    return { token: configured || "human-demo" };
  }
  if (!auth0) {
    return {
      error: jsonError(
        503,
        "authentication_unavailable",
        "Human sign-in is not configured for this environment.",
      ),
    };
  }
  try {
    const { token } = await auth0.getAccessToken();
    return { token };
  } catch {
    return {
      error: jsonError(401, "authentication_required", "Sign in to continue."),
    };
  }
}

function jsonError(status: number, code: string, message: string): Response {
  return Response.json(
    { error: { code, message } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

function responseHeaders(response: Response): Headers {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Type": "application/json",
  });
  const requestId = response.headers.get("X-Request-ID");
  if (requestId) headers.set("X-Request-ID", requestId);
  return headers;
}

function validMutationRequest(request: Request): Response | null {
  if (request.headers.get("X-Incident-Ops-Intent") !== "human-mutation") {
    return jsonError(
      403,
      "csrf_rejected",
      "The request origin could not be verified.",
    );
  }
  const origin = request.headers.get("Origin");
  if (!origin || origin !== new URL(request.url).origin) {
    return jsonError(
      403,
      "csrf_rejected",
      "The request origin could not be verified.",
    );
  }
  const idempotencyKey = request.headers.get("Idempotency-Key");
  if (!idempotencyKey || !UUID_PATTERN.test(idempotencyKey)) {
    return jsonError(
      422,
      "validation_error",
      "A valid idempotency key is required.",
    );
  }
  const statedLength = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(statedLength) && statedLength > MAX_MUTATION_BYTES) {
    return jsonError(413, "request_too_large", "The request is too large.");
  }
  return null;
}

async function callApi(
  request: Request,
  apiPath: string,
  init: RequestInit,
): Promise<Response> {
  const access = await humanAccessToken();
  if ("error" in access) return access.error;

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${access.token}`);
  const idempotencyKey = request.headers.get("Idempotency-Key");
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);

  try {
    const response = await fetch(`${apiBaseUrl()}${apiPath}`, {
      ...init,
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(10_000),
    });
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: responseHeaders(response),
    });
  } catch {
    return jsonError(
      503,
      "upstream_unavailable",
      "Incident Ops is temporarily unavailable.",
    );
  }
}

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}

export async function forwardQuery(
  request: Request,
  apiPath: string,
): Promise<Response> {
  const query = new URL(request.url).search;
  return callApi(request, `${apiPath}${query}`, { method: "GET" });
}

export async function forwardMutation(
  request: Request,
  apiPath: string,
  method: "POST" | "PATCH",
): Promise<Response> {
  const rejection = validMutationRequest(request);
  if (rejection) return rejection;

  let body: string;
  try {
    body = await request.text();
    if (new TextEncoder().encode(body).byteLength > MAX_MUTATION_BYTES) {
      return jsonError(413, "request_too_large", "The request is too large.");
    }
    if (body) JSON.parse(body);
  } catch {
    return jsonError(
      400,
      "invalid_json",
      "The request body must be valid JSON.",
    );
  }

  const init: RequestInit = { method };
  if (body) {
    init.body = body;
    init.headers = { "Content-Type": "application/json" };
  }
  return callApi(request, apiPath, init);
}

export function invalidIdentifier(): Response {
  return jsonError(404, "not_found", "The requested resource was not found.");
}

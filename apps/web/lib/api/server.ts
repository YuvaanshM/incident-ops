import "server-only";

import { auth0 } from "@/lib/auth0";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_MUTATION_BYTES = 24_000;

export function apiBaseUrl(): string {
  return (
    process.env.API_INTERNAL_URL ??
    process.env.API_PUBLIC_URL ??
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
}

export async function humanAccessToken(): Promise<
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

export function jsonError(
  status: number,
  code: string,
  message: string,
): Response {
  return Response.json(
    { error: { code, message } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export function hasTrustedOrigin(request: Request): boolean {
  try {
    // Deployment configuration owns the public origin; forwarding headers do not.
    const origin = new URL(process.env.APP_BASE_URL || request.url).origin;
    return request.headers.get("Origin") === origin;
  } catch {
    return false;
  }
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
  if (!hasTrustedOrigin(request)) {
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
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]),
      redirect: "error",
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

export async function forwardAdvisoryQuery(
  request: Request,
  apiPath: string,
): Promise<Response> {
  if (
    !hasTrustedOrigin(request) ||
    request.headers.get("X-Incident-Ops-Intent") !== "human-query"
  )
    return jsonError(
      403,
      "csrf_rejected",
      "The request origin could not be verified.",
    );
  if (
    request.headers.get("Content-Type")?.split(";")[0]?.trim() !==
    "application/json"
  )
    return jsonError(
      415,
      "unsupported_media_type",
      "A JSON request is required.",
    );
  const maxBytes = 12_000;
  if (Number(request.headers.get("Content-Length")) > maxBytes)
    return jsonError(413, "request_too_large", "The request is too large.");
  const reader = request.body?.getReader();
  if (!reader)
    return jsonError(400, "invalid_json", "A JSON request is required.");
  let body = "";
  let size = 0;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        return jsonError(413, "request_too_large", "The request is too large.");
      }
      body += decoder.decode(chunk.value, { stream: true });
    }
    body += decoder.decode();
    JSON.parse(body);
  } catch {
    return jsonError(
      400,
      "invalid_json",
      "The request body must be valid JSON.",
    );
  } finally {
    reader.releaseLock();
  }
  return callApi(request, apiPath, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json" },
  });
}

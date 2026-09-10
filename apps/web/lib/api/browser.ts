import { ApiRequestError, normalizeApiError } from "./errors";

export interface RequestJsonOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  mutation?: boolean;
  idempotencyKey?: string;
}

async function readJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return null;
  }
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function requestJson<T>(
  path: string,
  options: RequestJsonOptions = {},
): Promise<T> {
  const { body: bodyValue, mutation, idempotencyKey, ...requestInit } = options;
  const headers = new Headers(requestInit.headers);
  headers.set("Accept", "application/json");
  let body: string | undefined;

  if (bodyValue !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(bodyValue);
  }
  if (mutation) {
    headers.set("X-Incident-Ops-Intent", "human-mutation");
    headers.set("Idempotency-Key", idempotencyKey ?? crypto.randomUUID());
  }

  const fetchInit: RequestInit = { ...requestInit, headers };
  if (body !== undefined) fetchInit.body = body;
  const response = await fetch(path, fetchInit);
  const responseBody = await readJson(response);
  if (!response.ok) {
    throw new ApiRequestError(
      normalizeApiError(
        responseBody,
        response.status,
        response.headers.get("X-Request-ID"),
      ),
    );
  }
  return responseBody as T;
}

export function toApiError(error: unknown) {
  if (error instanceof ApiRequestError) {
    return error.apiError;
  }
  return {
    code: "network_error",
    message:
      "The console could not reach Incident Ops. Check your connection and try again.",
    status: 0,
  };
}

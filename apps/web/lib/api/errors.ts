export interface ApiFieldError {
  location: (string | number)[];
  type: string;
  message: string;
}

export interface ApiErrorDetails {
  fields?: ApiFieldError[];
  [key: string]: unknown;
}

export interface ApiError {
  code: string;
  message: string;
  status: number;
  requestId?: string;
  details?: ApiErrorDetails;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFieldError(value: unknown): value is ApiFieldError {
  return (
    isRecord(value) &&
    Array.isArray(value.location) &&
    value.location.every(
      (part) => typeof part === "string" || typeof part === "number",
    ) &&
    typeof value.type === "string" &&
    typeof value.message === "string"
  );
}

function normalizeDetails(value: unknown): ApiErrorDetails | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const details: ApiErrorDetails = { ...value };
  if ("fields" in value) {
    if (Array.isArray(value.fields)) {
      details.fields = value.fields.filter(isFieldError);
    } else {
      delete details.fields;
    }
  }
  return details;
}

export function normalizeApiError(
  body: unknown,
  status: number,
  requestId: string | null,
): ApiError {
  if (isRecord(body) && isRecord(body.error)) {
    const code = body.error.code;
    const message = body.error.message;
    if (typeof code === "string" && typeof message === "string") {
      const normalized: ApiError = { code, message, status };
      if (requestId) {
        normalized.requestId = requestId;
      }
      const details = normalizeDetails(body.error.details);
      if (details) {
        normalized.details = details;
      }
      return normalized;
    }
  }

  return {
    code: "request_failed",
    message: "The request could not be completed.",
    status,
  };
}

export class ApiRequestError extends Error {
  readonly apiError: ApiError;

  constructor(apiError: ApiError) {
    super(apiError.message);
    this.name = "ApiRequestError";
    this.apiError = apiError;
  }
}

export function fieldErrors(error: ApiError | null): Record<string, string> {
  const result: Record<string, string> = {};
  for (const field of error?.details?.fields ?? []) {
    const key = field.location.at(-1);
    if (typeof key === "string" && !(key in result)) {
      result[key] = field.message;
    }
  }
  return result;
}

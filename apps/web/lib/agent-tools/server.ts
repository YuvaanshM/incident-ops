import "server-only";

import {
  apiBaseUrl,
  hasTrustedOrigin,
  humanAccessToken,
  jsonError,
} from "@/lib/api/server";
import {
  parseToolCall,
  TOOL_DEFINITIONS,
  UUID_PATTERN,
  type ToolCall,
} from "@/lib/webmcp/contracts";

const MAX_BYTES = 12_000;
class BodyError extends Error {
  constructor(readonly status: number) {
    super("Invalid request body");
  }
}

async function readInput(request: Request): Promise<unknown> {
  if (
    !hasTrustedOrigin(request) ||
    request.headers.get("X-Incident-Ops-Intent") !== "agent-tool"
  )
    throw new BodyError(403);
  if (
    request.headers.get("Content-Type")?.split(";")[0]?.trim() !==
    "application/json"
  )
    throw new BodyError(415);
  if (Number(request.headers.get("Content-Length")) > MAX_BYTES)
    throw new BodyError(413);
  const reader = request.body?.getReader();
  if (!reader) throw new BodyError(400);
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let text = "";
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > MAX_BYTES) {
        await reader.cancel();
        throw new BodyError(413);
      }
      text += decoder.decode(chunk.value, { stream: true });
    }
    return JSON.parse(text + decoder.decode());
  } finally {
    reader.releaseLock();
  }
}

function invalidInput(error: unknown): Response {
  return jsonError(
    error instanceof BodyError ? error.status : 422,
    "invalid_input",
    "The tool request is invalid.",
  );
}

async function callAgent(
  request: Request,
  call: Pick<ToolCall, "path" | "method" | "body">,
  key?: string,
  timeoutMs = 10_000,
): Promise<Response> {
  const access = await humanAccessToken();
  if ("error" in access) return access.error;
  const signal = AbortSignal.any([
    request.signal,
    AbortSignal.timeout(timeoutMs),
  ]);
  try {
    signal.throwIfAborted();
    const exchange = await fetch(`${apiBaseUrl()}/api/v1/agent-sessions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${access.token}`,
        "Content-Type": "application/json",
      },
      body: "{}",
      cache: "no-store",
      redirect: "error",
      signal,
    });
    if (!exchange.ok)
      return jsonError(
        exchange.status,
        "delegation_unavailable",
        "Agent access is unavailable.",
      );
    const grant: unknown = await exchange.json();
    if (
      !grant ||
      typeof grant !== "object" ||
      !("access_token" in grant) ||
      typeof grant.access_token !== "string" ||
      !grant.access_token ||
      grant.access_token.length > 8192 ||
      !("token_type" in grant) ||
      grant.token_type !== "Bearer" ||
      !("expires_in" in grant) ||
      typeof grant.expires_in !== "number" ||
      grant.expires_in <= 0 ||
      grant.expires_in > 300
    )
      throw new Error("Invalid grant");
    const headers = new Headers({
      Authorization: `Bearer ${grant.access_token}`,
      Accept: "application/json",
    });
    if (key) headers.set("Idempotency-Key", key);
    if (call.body) headers.set("Content-Type", "application/json");
    const response = await fetch(`${apiBaseUrl()}${call.path}`, {
      method: call.method,
      headers,
      ...(call.body ? { body: JSON.stringify(call.body) } : {}),
      cache: "no-store",
      redirect: "error",
      signal,
    });
    if (!response.headers.get("Content-Type")?.includes("application/json"))
      throw new Error("Invalid response");
    const outputHeaders = new Headers({ "Cache-Control": "no-store" });
    for (const name of ["X-Request-ID", "Retry-After", "Idempotent-Replayed"]) {
      const value = response.headers.get(name);
      if (value) outputHeaders.set(name, value);
    }
    return Response.json(await response.json(), {
      status: response.status,
      headers: outputHeaders,
    });
  } catch {
    return request.signal.aborted
      ? jsonError(
          499,
          "cancelled",
          "No final result was received. A submitted command may still commit.",
        )
      : jsonError(
          503,
          "upstream_unavailable",
          "Incident Ops is temporarily unavailable.",
        );
  }
}

export async function forwardAgentTool(
  request: Request,
  name: string,
): Promise<Response> {
  if (!TOOL_DEFINITIONS.some((tool) => tool.name === name))
    return jsonError(404, "not_found", "Tool not found.");
  let call: ToolCall;
  let key: string | undefined;
  try {
    call = parseToolCall(name, await readInput(request));
    if (call.mutating) {
      key = request.headers.get("Idempotency-Key") ?? undefined;
      if (!key || !UUID_PATTERN.test(key)) throw new BodyError(422);
    }
  } catch (error) {
    return invalidInput(error);
  }
  return callAgent(request, call, key);
}

export async function recoverAgentRequest(
  request: Request,
  key: string,
): Promise<Response> {
  try {
    const body = await readInput(request);
    if (
      !UUID_PATTERN.test(key) ||
      !body ||
      typeof body !== "object" ||
      Array.isArray(body) ||
      Object.keys(body).length
    )
      throw new BodyError(422);
  } catch (error) {
    return invalidInput(error);
  }
  return callAgent(request, {
    method: "GET",
    path: `/api/v1/actions/by-request/${encodeURIComponent(key)}`,
  });
}

export async function forwardInvestigation(
  request: Request,
  incidentId: string,
): Promise<Response> {
  let body: {
    expected_incident_version: number;
    allow_action_requests: boolean;
  };
  let key: string;
  try {
    const input = await readInput(request);
    key = request.headers.get("Idempotency-Key") ?? "";
    if (
      !UUID_PATTERN.test(incidentId) ||
      !UUID_PATTERN.test(key) ||
      !input ||
      typeof input !== "object" ||
      Array.isArray(input)
    )
      throw new BodyError(422);
    const values = input as Record<string, unknown>;
    if (
      Object.keys(values).some(
        (name) =>
          !["expected_incident_version", "allow_action_requests"].includes(
            name,
          ),
      ) ||
      typeof values.expected_incident_version !== "number" ||
      !Number.isInteger(values.expected_incident_version) ||
      values.expected_incident_version < 1 ||
      values.expected_incident_version > 2147483647 ||
      (values.allow_action_requests !== undefined &&
        typeof values.allow_action_requests !== "boolean")
    )
      throw new BodyError(422);
    body = {
      expected_incident_version: values.expected_incident_version,
      allow_action_requests: values.allow_action_requests === true,
    };
  } catch (error) {
    return invalidInput(error);
  }
  return callAgent(
    request,
    {
      method: "POST",
      path: `/api/v1/incidents/${incidentId}/investigations`,
      body,
    },
    key,
    50_000,
  );
}

export async function recoverInvestigation(
  request: Request,
  key: string,
): Promise<Response> {
  try {
    const body = await readInput(request);
    if (
      !UUID_PATTERN.test(key) ||
      !body ||
      typeof body !== "object" ||
      Array.isArray(body) ||
      Object.keys(body).length
    )
      throw new BodyError(422);
  } catch (error) {
    return invalidInput(error);
  }
  return callAgent(request, {
    method: "GET",
    path: `/api/v1/investigations/by-request/${key}`,
  });
}

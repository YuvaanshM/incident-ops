import {
  parseToolCall,
  TOOL_DEFINITIONS,
  UUID_PATTERN,
  type ToolName,
} from "./contracts";

export type ToolResult = {
  outcome: string;
  data?: unknown;
  error?: { code: string; message: string };
  request?: {
    idempotency_key: string;
    recovery_path: string;
    commit_status: "unknown" | "known";
  };
};
export type RegisteredDefinition = {
  name: string;
  description: string;
  inputSchema: object;
  execute: (
    input: unknown,
    options: { signal: AbortSignal },
  ) => Promise<ToolResult>;
  annotations: {
    readOnlyHint: boolean;
    untrustedContentHint: boolean;
    consequentialHint: boolean;
  };
};
export type ModelContext = {
  registerTool: (
    tool: RegisteredDefinition,
    options: { signal: AbortSignal; exposedTo: string[] },
  ) => Promise<void>;
};

function responseOutcome(status: number, body: unknown): string {
  if (status >= 200 && status < 300) {
    const state =
      body && typeof body === "object" && "status" in body ? body.status : null;
    return typeof state === "string" &&
      ["applied", "pending", "rejected", "failed", "stale"].includes(state)
      ? state
      : "ok";
  }
  if (status === 401 || status === 403) return "denied";
  if (status === 409) return "version_conflict";
  if (status === 404) return "not_found";
  if (status === 429) return "rate_limited";
  if (status === 499) return "cancelled";
  if (status === 400 || status === 422) return "invalid_input";
  return "unavailable";
}

async function postTool(
  path: string,
  input: unknown,
  signal: AbortSignal,
  key?: string,
  recovering = false,
): Promise<ToolResult> {
  const recovery: ToolResult["request"] = key
    ? {
        idempotency_key: key,
        recovery_path: `/api/agent-tools/requests/${key}`,
        commit_status: "unknown",
      }
    : undefined;
  try {
    signal.throwIfAborted();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Incident-Ops-Intent": "agent-tool",
    };
    if (key) headers["Idempotency-Key"] = key;
    const response = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify(input),
      signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    if (
      !(response.headers.get("Content-Type") ?? "").includes("application/json")
    )
      throw new Error("invalid response");
    const data: unknown = await response.json();
    const result: ToolResult = {
      outcome: responseOutcome(response.status, data),
      data,
    };
    if (recovery)
      result.request = {
        ...recovery,
        commit_status:
          response.status >= 499 || (recovering && !response.ok)
            ? "unknown"
            : "known",
      };
    return result;
  } catch {
    const result: ToolResult = {
      outcome: signal.aborted ? "cancelled" : "network_failure",
      error: {
        code: signal.aborted ? "cancelled" : "network_failure",
        message:
          "No final result was received. A submitted command may still commit.",
      },
    };
    if (recovery) result.request = recovery;
    return result;
  }
}

export async function executeToolCall(
  name: ToolName | string,
  input: unknown,
  signal: AbortSignal,
): Promise<ToolResult> {
  try {
    const call = parseToolCall(name, input);
    return await postTool(
      `/api/agent-tools/${call.name}`,
      input,
      signal,
      call.mutating ? crypto.randomUUID() : undefined,
    );
  } catch {
    return {
      outcome: "invalid_input",
      error: {
        code: "invalid_input",
        message: "The tool input does not match its schema.",
      },
    };
  }
}

export function recoverToolRequest(
  key: string,
  signal: AbortSignal,
): Promise<ToolResult> {
  if (!UUID_PATTERN.test(key))
    return Promise.resolve({ outcome: "invalid_input" });
  return postTool(
    `/api/agent-tools/requests/${encodeURIComponent(key)}`,
    {},
    signal,
    key,
    true,
  );
}

export function registerIncidentTools(
  context: ModelContext,
  report?: (result: ToolResult) => void,
): () => void {
  const controllers = TOOL_DEFINITIONS.map(() => new AbortController());
  let disposed = false;
  const cleanup = () => {
    disposed = true;
    controllers.forEach((controller) => controller.abort());
  };
  void (async () => {
    try {
      for (const [index, definition] of TOOL_DEFINITIONS.entries()) {
        if (disposed) break;
        await context.registerTool(
          {
            name: definition.name,
            description: definition.description,
            inputSchema: definition.inputSchema,
            annotations: {
              readOnlyHint: !definition.mutating,
              untrustedContentHint: true,
              consequentialHint: definition.mutating,
            },
            execute: async (input, { signal }) => {
              const result = await executeToolCall(
                definition.name,
                input,
                signal,
              );
              if (!disposed && definition.mutating) report?.(result);
              return result;
            },
          },
          { signal: controllers[index]!.signal, exposedTo: [] },
        );
      }
    } catch {
      cleanup();
    }
  })();
  return cleanup;
}

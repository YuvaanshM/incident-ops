import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const auth = vi.hoisted(() => ({ getAccessToken: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth0", () => ({ auth0: auth }));
import {
  forwardAgentTool,
  recoverAgentRequest,
  forwardInvestigation,
  recoverInvestigation,
} from "@/lib/agent-tools/server";

const incidentId = "10000000-0000-4000-8000-000000000001";
const jobId = "20000000-0000-4000-8000-000000000001";
const key = "40000000-0000-4000-8000-000000000001";
const input = {
  incident_id: incidentId,
  job_id: jobId,
  expected_incident_version: 2,
  rationale: "Retry fixture",
};
const grant = () =>
  Response.json({
    access_token: "short-lived-agent-credential",
    expires_in: 300,
    token_type: "Bearer",
  });
function request(
  body: unknown = input,
  additions: Record<string, string> = {},
  signal?: AbortSignal,
) {
  return new Request(
    "https://app.example/api/agent-tools/request_retry_failed_job",
    {
      method: "POST",
      headers: {
        Origin: "https://app.example",
        "Content-Type": "application/json",
        "X-Incident-Ops-Intent": "agent-tool",
        "Idempotency-Key": key,
        Authorization: "Bearer attacker",
        ...additions,
      },
      body: JSON.stringify(body),
      ...(signal ? { signal } : {}),
    },
  );
}
beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("API_INTERNAL_URL", "https://api.internal.example");
  auth.getAccessToken.mockResolvedValue({ token: "verified-human-session" });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
describe("server-side agent transport", () => {
  it("uses the same delegated transport for bounded investigation and recovery", async () => {
    const upstream = vi
      .fn()
      .mockResolvedValueOnce(grant())
      .mockResolvedValueOnce(
        Response.json({ outcome: { status: "completed" } }),
      )
      .mockResolvedValueOnce(grant())
      .mockResolvedValueOnce(
        Response.json({ outcome: { status: "completed" } }),
      );
    vi.stubGlobal("fetch", upstream);
    const body = { expected_incident_version: 2, allow_action_requests: false };
    expect((await forwardInvestigation(request(body), incidentId)).status).toBe(
      200,
    );
    const [path, init] = upstream.mock.calls[1] as [string, RequestInit];
    expect(path).toBe(
      `https://api.internal.example/api/v1/incidents/${incidentId}/investigations`,
    );
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer short-lived-agent-credential",
    );
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(key);
    expect(JSON.parse(String(init.body))).toEqual(body);
    expect((await recoverInvestigation(request({}), key)).status).toBe(200);
    expect(upstream.mock.calls[3]![0]).toBe(
      `https://api.internal.example/api/v1/investigations/by-request/${key}`,
    );
  });
  it.each([
    { expected_incident_version: 2, actor_type: "human" },
    { expected_incident_version: true },
    { expected_incident_version: 0 },
    { expected_incident_version: 2, allow_action_requests: "true" },
  ])(
    "rejects untrusted investigation fields before delegation",
    async (body) => {
      const upstream = vi.fn();
      vi.stubGlobal("fetch", upstream);
      expect(
        (await forwardInvestigation(request(body), incidentId)).status,
      ).toBe(422);
      expect(upstream).not.toHaveBeenCalled();
    },
  );
  it("validates the configured public origin behind an internal Next.js host", async () => {
    vi.stubEnv("APP_BASE_URL", "https://app.example");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(grant())
        .mockResolvedValueOnce(Response.json({ status: "pending" })),
    );
    const incoming = request();
    const internal = new Request(
      "http://localhost:3108/api/agent-tools/request_retry_failed_job",
      {
        method: "POST",
        headers: incoming.headers,
        body: JSON.stringify(input),
      },
    );
    expect(
      (await forwardAgentTool(internal, "request_retry_failed_job")).status,
    ).toBe(200);
    vi.unstubAllEnvs();
  });
  it("exchanges the human credential then forwards only agent authority and original idempotency", async () => {
    const upstream = vi
      .fn()
      .mockResolvedValueOnce(grant())
      .mockResolvedValueOnce(
        Response.json({ id: jobId, status: "pending" }, { status: 201 }),
      );
    vi.stubGlobal("fetch", upstream);
    const response = await forwardAgentTool(
      request(),
      "request_retry_failed_job",
    );
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: jobId, status: "pending" });
    const [exchangePath, exchangeInit] = upstream.mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(exchangePath).toBe(
      "https://api.internal.example/api/v1/agent-sessions",
    );
    expect(new Headers(exchangeInit.headers).get("Authorization")).toBe(
      "Bearer verified-human-session",
    );
    expect(exchangeInit.body).toBe("{}");
    const [path, init] = upstream.mock.calls[1] as [string, RequestInit];
    expect(path).toBe(
      `https://api.internal.example/api/v1/incidents/${incidentId}/actions`,
    );
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer short-lived-agent-credential",
    );
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(key);
    expect(JSON.parse(String(init.body))).toEqual({
      expected_incident_version: 2,
      action_type: "retry_failed_job",
      parameters: { job_id: jobId },
      rationale: input.rationale,
    });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });
  it.each([
    { Origin: "https://evil.example" },
    { Origin: "" },
    { "X-Incident-Ops-Intent": "human-mutation" },
    { "Content-Type": "text/plain" },
  ])("rejects invalid CSRF/content evidence", async (headers) => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    expect(
      (
        await forwardAgentTool(
          request(input, headers),
          "request_retry_failed_job",
        )
      ).status,
    ).toBeGreaterThanOrEqual(400);
    expect(upstream).not.toHaveBeenCalled();
  });
  it.each(["approve_action", "close_incident", "execute", "../actions"])(
    "has no %s transport",
    async (name) => {
      const upstream = vi.fn();
      vi.stubGlobal("fetch", upstream);
      expect((await forwardAgentTool(request(), name)).status).toBe(404);
      expect(upstream).not.toHaveBeenCalled();
    },
  );
  it("refuses authority fields, invalid keys and oversized bodies before minting a credential", async () => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    expect(
      (
        await forwardAgentTool(
          request({ ...input, actor_type: "human" }),
          "request_retry_failed_job",
        )
      ).status,
    ).toBe(422);
    expect(
      (
        await forwardAgentTool(
          request(input, { "Idempotency-Key": "invalid" }),
          "request_retry_failed_job",
        )
      ).status,
    ).toBe(422);
    expect(
      (
        await forwardAgentTool(
          request({ ...input, rationale: "x".repeat(15000) }),
          "request_retry_failed_job",
        )
      ).status,
    ).toBe(413);
    expect(upstream).not.toHaveBeenCalled();
  });
  it("does not fall back to the human credential when delegation fails", async () => {
    const upstream = vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { error: { code: "forbidden", message: "Denied" } },
          { status: 403 },
        ),
      );
    vi.stubGlobal("fetch", upstream);
    const result = await forwardAgentTool(
      request(),
      "request_retry_failed_job",
    );
    expect(result.status).toBe(403);
    expect(upstream).toHaveBeenCalledOnce();
    expect(await result.text()).not.toContain("credential");
  });
  it("never contacts the API without a hosted human session", async () => {
    auth.getAccessToken.mockRejectedValue(
      new Error("private hosted auth failure"),
    );
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    expect(
      (await forwardAgentTool(request(), "request_retry_failed_job")).status,
    ).toBe(401);
    expect(upstream).not.toHaveBeenCalled();
  });
  it("passes cancellation through exchange and command fetches", async () => {
    const controller = new AbortController();
    const ready = Promise.withResolvers<void>();
    const signals: AbortSignal[] = [];
    const upstream = vi
      .fn()
      .mockImplementationOnce((_url, init) => {
        signals.push(init.signal);
        return Promise.resolve(grant());
      })
      .mockImplementationOnce((_url, init) => {
        signals.push(init.signal);
        ready.resolve();
        return new Promise((_resolve, reject) => {
          init.signal.addEventListener(
            "abort",
            () => reject(new DOMException("Cancelled", "AbortError")),
            { once: true },
          );
        });
      });
    vi.stubGlobal("fetch", upstream);
    const result = forwardAgentTool(
      request(input, {}, controller.signal),
      "request_retry_failed_job",
    );
    await ready.promise;
    controller.abort();
    expect((await result).status).toBe(499);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });
  it("queries a fixed principal-scoped recovery route using agent authority", async () => {
    const upstream = vi
      .fn()
      .mockResolvedValueOnce(grant())
      .mockResolvedValueOnce(Response.json({ id: jobId, status: "applied" }));
    vi.stubGlobal("fetch", upstream);
    expect((await recoverAgentRequest(request({}), key)).status).toBe(200);
    expect(upstream.mock.calls[1]?.[0]).toBe(
      `https://api.internal.example/api/v1/actions/by-request/${key}`,
    );
  });
  it("sanitizes network failures instead of returning secret-bearing diagnostics", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValue(
          new Error("short-lived-agent-credential private details"),
        ),
    );
    const response = await forwardAgentTool(
      request(),
      "request_retry_failed_job",
    );
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("private");
  });
});

it("returns cited retrieval as agent data without forwarding human or mutation authority", async () => {
  const upstream = vi
    .fn()
    .mockResolvedValueOnce(grant())
    .mockResolvedValueOnce(
      Response.json({
        advisory: true,
        suggestions: [{ source_incident_id: incidentId }],
      }),
    );
  vi.stubGlobal("fetch", upstream);
  const response = await forwardAgentTool(
    request({ incident_id: incidentId }),
    "suggest_remediation",
  );
  expect(response.status).toBe(200);
  const [path, init] = upstream.mock.calls[1] as [string, RequestInit];
  expect(path).toBe(
    `https://api.internal.example/api/v1/incidents/${incidentId}/remediation-suggestions`,
  );
  expect(new Headers(init.headers).get("Authorization")).toBe(
    "Bearer short-lived-agent-credential",
  );
  expect(new Headers(init.headers).has("Idempotency-Key")).toBe(false);
  expect(init.body).toBe("{}");
});

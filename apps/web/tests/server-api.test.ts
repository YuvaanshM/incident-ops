import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ getAccessToken: vi.fn() }));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth0", () => ({ auth0: auth }));

import { forwardMutation, forwardQuery } from "@/lib/api/server";

describe("server-side API transport", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("API_INTERNAL_URL", "https://api.internal.example");
  });

  it("uses the hosted session token and never forwards browser authorization", async () => {
    auth.getAccessToken.mockResolvedValue({
      token: "verified-hosted-access-token",
      expiresAt: 1_800_000_000,
    });
    const upstream = vi
      .fn()
      .mockResolvedValue(Response.json({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", upstream);
    const request = new Request(
      "https://app.example/api/ui/actions?status=pending",
      {
        headers: { Authorization: "Bearer browser-controlled-token" },
      },
    );

    const response = await forwardQuery(request, "/api/v1/actions");

    expect(response.status).toBe(200);
    expect(auth.getAccessToken).toHaveBeenCalledOnce();
    const [, init] = upstream.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe(
      "Bearer verified-hosted-access-token",
    );
    expect(headers.get("Authorization")).not.toContain("browser-controlled");
  });

  it("fails closed when the hosted session is missing", async () => {
    auth.getAccessToken.mockRejectedValue(new Error("missing session"));
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);

    const response = await forwardQuery(
      new Request("https://app.example/api/ui/incidents"),
      "/api/v1/incidents",
    );

    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("authentication_required");
    expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects a mutation without exact same-origin CSRF evidence", async () => {
    auth.getAccessToken.mockResolvedValue({
      token: "verified-hosted-access-token",
      expiresAt: 1_800_000_000,
    });
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);

    const response = await forwardMutation(
      new Request("https://app.example/api/ui/incidents", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": "40000000-0000-4000-8000-000000000001",
          "X-Incident-Ops-Intent": "human-mutation",
        },
        body: "{}",
      }),
      "/api/v1/incidents",
      "POST",
    );

    expect(response.status).toBe(403);
    expect((await response.json()).error.code).toBe("csrf_rejected");
    expect(upstream).not.toHaveBeenCalled();
  });
});

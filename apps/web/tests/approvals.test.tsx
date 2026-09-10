import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApprovalDetailScreen } from "@/components/approvals/approval-detail-screen";
import { ApprovalQueueScreen } from "@/components/approvals/approval-queue-screen";

import { incident, pendingAction } from "./fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApprovalQueueScreen", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders pending decisions with incident context and a shareable filter", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((path: string) => {
        if (path.startsWith("/api/ui/actions")) {
          return Promise.resolve(
            jsonResponse({ items: [pendingAction], next_cursor: null }),
          );
        }
        return Promise.resolve(jsonResponse(incident));
      }),
    );

    render(<ApprovalQueueScreen initialStatus="pending" />);

    expect(
      await screen.findByRole("link", { name: "Retry failed job" }),
    ).toHaveAttribute("href", `/approvals/${pendingAction.id}`);
    expect(screen.getByRole("link", { name: incident.title })).toHaveAttribute(
      "href",
      `/incidents/${incident.id}`,
    );
    expect(
      screen.getByText("Pending", { selector: ".badge" }),
    ).toBeInTheDocument();
    const filter = screen.getByRole("search");
    expect(filter).toHaveAttribute("method", "get");
    expect(filter).toHaveAttribute("action", "/approvals");
    expect(screen.getByLabelText("Action status")).toHaveValue("pending");
  });

  it("sends incident and age filters through the shareable queue URL", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ApprovalQueueScreen
        initialIncidentId={incident.id}
        initialMinimumAgeMinutes={60}
        initialStatus="pending"
      />,
    );

    await screen.findByRole("heading", {
      name: "No approval requests match this filter",
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      `incident_id=${incident.id}`,
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "minimum_age_minutes=60",
    );
    expect(screen.getByLabelText("Incident ID")).toHaveValue(incident.id);
    expect(screen.getByLabelText("Minimum action age")).toHaveValue("60");
  });

  it("explains forbidden queues without exposing decision controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "forbidden",
              message:
                "The verified principal does not have the required scope.",
            },
          },
          403,
        ),
      ),
    );

    render(<ApprovalQueueScreen initialStatus="pending" />);

    expect(
      await screen.findByRole("heading", { name: "Approval access required" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
  });
});

describe("ApprovalDetailScreen", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows request, rationale, parameters, policy, and incident context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((path: string) => {
        if (path.includes("/actions/")) {
          return Promise.resolve(jsonResponse(pendingAction));
        }
        return Promise.resolve(jsonResponse(incident));
      }),
    );

    render(<ApprovalDetailScreen actionId={pendingAction.id} />);

    expect(
      await screen.findByRole("heading", { name: "Retry failed job" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: incident.title })).toHaveAttribute(
      "href",
      `/incidents/${incident.id}`,
    );
    expect(
      screen.getByText(String(pendingAction.rationale)),
    ).toBeInTheDocument();
    expect(screen.getByText("Job id")).toBeInTheDocument();
    expect(screen.getByText("Require approval")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve request" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Reject request" }),
    ).toBeEnabled();
  });

  it("approves through one idempotent mutation and replaces pending controls", async () => {
    const applied = {
      ...pendingAction,
      status: "applied" as const,
      outcome_code: "job_retried",
      decided_by_type: "human",
      decided_by_id: "operator@example.com",
      decided_at: "2026-09-09T12:45:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string, init?: RequestInit) => {
        if (path.endsWith("/approve") && init?.method === "POST") {
          return Promise.resolve(jsonResponse(applied));
        }
        if (path.includes("/actions/"))
          return Promise.resolve(jsonResponse(pendingAction));
        return Promise.resolve(jsonResponse(incident));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<ApprovalDetailScreen actionId={pendingAction.id} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve request" }),
    );

    expect(
      await screen.findByText("This request was applied."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve request" }),
    ).not.toBeInTheDocument();
    const approveCall = fetchMock.mock.calls.find(([path]) =>
      String(path).endsWith("/approve"),
    ) as [string, RequestInit];
    const headers = new Headers(approveCall[1].headers);
    expect(headers.get("Idempotency-Key")).toEqual(expect.any(String));
  });

  it("refreshes an approval race and explains the other operator's decision", async () => {
    const rejected = {
      ...pendingAction,
      status: "rejected" as const,
      outcome_code: "human_rejected",
      decided_by_type: "human",
      decided_by_id: "other-operator@example.com",
      decided_at: "2026-09-09T12:46:00Z",
    };
    let actionReads = 0;
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string, init?: RequestInit) => {
        if (path.endsWith("/approve") && init?.method === "POST") {
          return Promise.resolve(
            jsonResponse(
              {
                error: {
                  code: "action_not_pending",
                  message: "The action is no longer pending.",
                  details: { current_status: "rejected" },
                },
              },
              409,
            ),
          );
        }
        if (path.includes("/actions/")) {
          actionReads += 1;
          return Promise.resolve(
            jsonResponse(actionReads === 1 ? pendingAction : rejected),
          );
        }
        return Promise.resolve(jsonResponse(incident));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<ApprovalDetailScreen actionId={pendingAction.id} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve request" }),
    );

    expect(
      await screen.findByText(
        "Another operator already decided this request. The latest result is shown below.",
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByText("Rejected", { selector: ".badge" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Approve request" }),
    ).not.toBeInTheDocument();
  });
});

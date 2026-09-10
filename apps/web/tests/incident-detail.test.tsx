import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IncidentDetailScreen } from "@/components/incidents/incident-detail-screen";

import { incidentWithJob, pendingAction, timelineEvent } from "./fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("IncidentDetailScreen", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads authoritative incident, job, and audit context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((path: string) => {
        if (path.endsWith("/timeline?limit=20")) {
          return Promise.resolve(
            jsonResponse({ items: [timelineEvent], next_cursor: null }),
          );
        }
        return Promise.resolve(jsonResponse(incidentWithJob));
      }),
    );

    render(
      <IncidentDetailScreen demoToolsEnabled incidentId={incidentWithJob.id} />,
    );

    expect(
      await screen.findByRole("heading", { name: incidentWithJob.title }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Investigating").length).toBeGreaterThan(0);
    expect(screen.getByText("version 4")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Rebuild checkout search index" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Incident updated")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Simulate demo failure" }),
    ).toBeInTheDocument();
  });

  it("sends the rendered incident version when saving an edit", async () => {
    const updated = {
      ...incidentWithJob,
      description: "Checkout errors are now isolated to card payments.",
      version: 5,
    };
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string, init?: RequestInit) => {
        if (init?.method === "PATCH")
          return Promise.resolve(jsonResponse(updated));
        if (path.endsWith("/timeline?limit=20")) {
          return Promise.resolve(
            jsonResponse({ items: [], next_cursor: null }),
          );
        }
        return Promise.resolve(jsonResponse(incidentWithJob));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentDetailScreen incidentId={incidentWithJob.id} />);
    const description = await screen.findByLabelText("Description");
    fireEvent.change(description, { target: { value: updated.description } });
    fireEvent.click(screen.getByRole("button", { name: "Save incident" }));

    await screen.findByText("Incident saved at version 5.");
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    ) as [string, RequestInit];
    expect(JSON.parse(String(patchCall[1].body))).toMatchObject({
      expected_version: 4,
      description: updated.description,
    });
  });

  it("requests a retry and renders the backend-returned decision", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string, init?: RequestInit) => {
        if (path.endsWith("/actions") && init?.method === "POST") {
          return Promise.resolve(jsonResponse(pendingAction, 201));
        }
        if (path.endsWith("/timeline?limit=20")) {
          return Promise.resolve(
            jsonResponse({ items: [], next_cursor: null }),
          );
        }
        return Promise.resolve(jsonResponse(incidentWithJob));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentDetailScreen incidentId={incidentWithJob.id} />);
    fireEvent.change(await screen.findByLabelText("Retry rationale"), {
      target: { value: pendingAction.rationale },
    });
    fireEvent.click(screen.getByRole("button", { name: "Request retry" }));

    await screen.findByText("Retry request is pending human review.");
    expect(screen.getByRole("link", { name: "Review action" })).toHaveAttribute(
      "href",
      `/approvals/${pendingAction.id}`,
    );
    await waitFor(() => {
      const actionCall = fetchMock.mock.calls.find(([path]) =>
        String(path).endsWith("/actions"),
      ) as [string, RequestInit];
      expect(JSON.parse(String(actionCall[1].body))).toEqual({
        action_type: "retry_failed_job",
        expected_incident_version: 4,
        parameters: { job_id: incidentWithJob.jobs?.[0]?.id },
        rationale: pendingAction.rationale,
      });
    });
  });

  it("keeps a stale draft and requires review before deliberate resubmission", async () => {
    const latest = {
      ...incidentWithJob,
      description: "Another operator narrowed impact to US card payments.",
      version: 5,
    };
    const saved = {
      ...latest,
      description: "My preserved draft with confirmed database evidence.",
      version: 6,
    };
    let detailReads = 0;
    let patchWrites = 0;
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string, init?: RequestInit) => {
        if (init?.method === "PATCH") {
          patchWrites += 1;
          if (patchWrites === 1) {
            return Promise.resolve(
              jsonResponse(
                {
                  error: {
                    code: "version_conflict",
                    message:
                      "The incident changed before this update was applied.",
                    details: {
                      current_version: 5,
                      refetch: `/api/v1/incidents/${incidentWithJob.id}`,
                    },
                  },
                },
                409,
              ),
            );
          }
          return Promise.resolve(jsonResponse(saved));
        }
        if (path.endsWith("/timeline?limit=20")) {
          return Promise.resolve(
            jsonResponse({ items: [], next_cursor: null }),
          );
        }
        detailReads += 1;
        return Promise.resolve(
          jsonResponse(detailReads === 1 ? incidentWithJob : latest),
        );
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentDetailScreen incidentId={incidentWithJob.id} />);
    const description = await screen.findByLabelText("Description");
    fireEvent.change(description, { target: { value: saved.description } });
    fireEvent.click(screen.getByRole("button", { name: "Save incident" }));

    expect(
      await screen.findByRole("heading", { name: "Your draft is safe" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toHaveValue(saved.description);
    expect(
      screen.queryByRole("button", { name: "Resubmit my draft" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Review latest version" }),
    );
    expect(await screen.findByText("Latest version 5")).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("Latest incident version")).getByText(
        latest.description,
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toHaveValue(saved.description);

    fireEvent.click(screen.getByRole("button", { name: "Resubmit my draft" }));
    expect(
      await screen.findByText("Incident saved at version 6."),
    ).toBeInTheDocument();
    const patchCalls = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    ) as [string, RequestInit][];
    expect(JSON.parse(String(patchCalls[1]?.[1].body))).toMatchObject({
      expected_version: 5,
      description: saved.description,
    });
  });
});

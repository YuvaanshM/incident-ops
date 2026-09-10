import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IncidentListScreen } from "@/components/incidents/incident-list-screen";

import { incident } from "./fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("IncidentListScreen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a scannable incident ledger with written status and severity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [incident],
        next_cursor: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentListScreen initialFilters={{}} />);

    expect(
      (await screen.findAllByRole("link", { name: incident.title }))[0],
    ).toHaveAttribute("href", `/incidents/${incident.id}`);
    expect(screen.getAllByText("High").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Investigating").length).toBeGreaterThan(0);
    expect(screen.getAllByText("v4")).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ui/incidents?limit=20",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("keeps filters in a shareable GET form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ items: [], next_cursor: null })),
    );

    render(
      <IncidentListScreen
        initialFilters={{
          q: "latency",
          service: "checkout",
          severity: "high",
          status: "investigating",
        }}
      />,
    );

    await screen.findByText(/no incidents match/i);
    const form = screen.getByRole("search");
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/incidents");
    expect(screen.getByLabelText("Search incidents")).toHaveValue("latency");
    expect(screen.getByLabelText("Service")).toHaveValue("checkout");
    expect(screen.getByLabelText("Severity")).toHaveValue("high");
    expect(screen.getByLabelText("Status")).toHaveValue("investigating");
  });

  it("appends a cursor page without duplicating an incident", async () => {
    const nextIncident = {
      ...incident,
      id: "10000000-0000-4000-8000-000000000002",
      title: "Worker queue stalled",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ items: [incident], next_cursor: "cursor-page-2" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [incident, nextIncident],
          next_cursor: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentListScreen initialFilters={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));

    await screen.findAllByRole("link", { name: nextIncident.title });
    expect(screen.getAllByRole("link", { name: incident.title })).toHaveLength(
      2,
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/ui/incidents?cursor=cursor-page-2&limit=20",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("shows a retryable backend error", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "upstream_unavailable", message: "Unavailable" } },
          503,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ items: [incident], next_cursor: null }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentListScreen initialFilters={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(
      (await screen.findAllByRole("link", { name: incident.title }))[0],
    ).toBeInTheDocument();
  });
});

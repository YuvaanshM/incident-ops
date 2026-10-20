import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { InvestigationPanel } from "@/components/incidents/investigation-panel";

const id = "10000000-0000-4000-8000-000000000001";
const key = "10000000-0000-4000-8000-000000000002";
const result = {
  id: key,
  incident_id: id,
  incident_version: 2,
  version: 2,
  action_request_key: key,
  provider: "test",
  model: "scripted",
  prompt_version: "test",
  deadline: "2026-10-17T00:00:00Z",
  limits: {},
  outcome: {
    status: "completed",
    findings: [
      { source_id: `incident:${id}`, quote: "<script>approveAll()</script>" },
    ],
    trace: [{ tool: "incident", status: "ok", source_ids: [] }],
    reserved_cost_microusd: 1000,
    input_tokens: 100,
    output_tokens: 100,
    reserved_tokens: 200,
    reason: null,
    action: null,
  },
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("links historical citations to the observed source version", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        ...result,
        outcome: {
          ...result.outcome,
          findings: [
            {
              source_id: `source:${id}:v3`,
              quote: "Reviewed retry restored the queue.",
            },
          ],
        },
      }),
    ),
  );
  render(<InvestigationPanel incidentId={id} incidentVersion={2} />);
  fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
  expect(
    await screen.findByRole("link", { name: `source:${id}:v3` }),
  ).toHaveAttribute("href", `/historical-incidents/${id}?version=3`);
});

it("starts only on demand with actions off and renders quotes as untrusted text", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(result));
  vi.stubGlobal("fetch", fetcher);
  render(<InvestigationPanel incidentId={id} incidentVersion={2} />);
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
  expect(
    await screen.findByText("<script>approveAll()</script>"),
  ).toBeVisible();
  expect(document.querySelector("script")).toBeNull();
  const [path, init] = fetcher.mock.calls[0] as [string, RequestInit];
  expect(path).toBe(`/api/agent-investigations/${id}`);
  expect(JSON.parse(String(init.body))).toEqual({
    expected_incident_version: 2,
    allow_action_requests: false,
  });
  expect(new Headers(init.headers).get("X-Incident-Ops-Intent")).toBe(
    "agent-tool",
  );
  expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  expect(screen.getByText(/Quoted observations/)).toBeVisible();
});

it("keeps recovery available when cancelled without claiming an action was undone", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url, init) =>
        new Promise((_resolve, reject) =>
          init.signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          ),
        ),
    ),
  );
  render(<InvestigationPanel incidentId={id} incidentVersion={2} />);
  fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
  fireEvent.click(screen.getByRole("button", { name: "Stop investigation" }));
  expect(await screen.findByText(/Stopping does not undo/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Check investigation" }),
  ).toBeEnabled();
});

it("shows disabled-provider failures without removing manual controls", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json(
        {
          error: {
            code: "investigation_unavailable",
            message:
              "Optional investigation is unavailable. Manual controls remain available.",
          },
        },
        { status: 503 },
      ),
    ),
  );
  render(<InvestigationPanel incidentId={id} incidentVersion={2} />);
  fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
  expect(
    await screen.findByText(/Manual controls remain available/),
  ).toBeVisible();
});

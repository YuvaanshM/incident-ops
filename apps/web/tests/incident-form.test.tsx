import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IncidentForm } from "@/components/incidents/incident-form";

import { incident } from "./fixtures";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("IncidentForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves every draft field when backend validation fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "validation_error",
              message: "The request is invalid.",
              details: {
                fields: [
                  {
                    location: ["body", "title"],
                    type: "string_too_long",
                    message: "Title must be 200 characters or fewer.",
                  },
                ],
              },
            },
          },
          422,
        ),
      ),
    );

    render(<IncidentForm onCreated={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Incident title"), {
      target: { value: "Checkout is slow" },
    });
    fireEvent.change(screen.getByLabelText("Affected service"), {
      target: { value: "checkout" },
    });
    fireEvent.change(screen.getByLabelText("What is happening?"), {
      target: { value: "Customer requests are timing out." },
    });
    fireEvent.change(screen.getByLabelText("Severity"), {
      target: { value: "high" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create incident" }));

    expect(
      await screen.findByText("Title must be 200 characters or fewer."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Incident title")).toHaveValue(
      "Checkout is slow",
    );
    expect(screen.getByLabelText("Affected service")).toHaveValue("checkout");
    expect(screen.getByLabelText("What is happening?")).toHaveValue(
      "Customer requests are timing out.",
    );
    expect(screen.getByLabelText("Severity")).toHaveValue("high");
  });

  it("submits one idempotent command and reports the created incident", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(incident, 201));
    const onCreated = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<IncidentForm onCreated={onCreated} />);
    fireEvent.change(screen.getByLabelText("Incident title"), {
      target: { value: incident.title },
    });
    fireEvent.change(screen.getByLabelText("Affected service"), {
      target: { value: incident.service },
    });
    fireEvent.change(screen.getByLabelText("What is happening?"), {
      target: { value: incident.description },
    });
    fireEvent.change(screen.getByLabelText("Severity"), {
      target: { value: incident.severity },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create incident" }));

    await screen.findByRole("status");
    expect(onCreated).toHaveBeenCalledWith(incident);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(path).toBe("/api/ui/incidents");
    expect(init.method).toBe("POST");
    expect(headers.get("Idempotency-Key")).toEqual(expect.any(String));
    expect(headers.get("X-Incident-Ops-Intent")).toBe("human-mutation");
  });
});

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
const navigation = vi.hoisted(() => ({ path: "/incidents" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path }));
import { WebMcpBridge } from "@/components/webmcp-bridge";
import type { RegisteredDefinition } from "@/lib/webmcp/tools";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "modelContext");
});
describe("WebMCP page lifecycle", () => {
  it("leaves ordinary content usable in an unsupported browser", () => {
    render(
      <>
        <WebMcpBridge />
        <button>Human action</button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Human action" })).toBeVisible();
    expect(screen.queryByLabelText("Browser agent result")).toBeNull();
  });
  it("cleans registrations on navigation and unmount and shows uncertain mutation recovery", async () => {
    vi.stubGlobal("isSecureContext", true);
    const active = new Map<string, RegisteredDefinition>();
    const signals: AbortSignal[] = [];
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: {
        registerTool: vi.fn(
          async (
            tool: RegisteredDefinition,
            { signal }: { signal: AbortSignal },
          ) => {
            active.set(tool.name, tool);
            signals.push(signal);
            signal.addEventListener("abort", () => active.delete(tool.name), {
              once: true,
            });
          },
        ),
      },
    });
    const mounted = render(<WebMcpBridge />);
    await waitFor(() => expect(active.size).toBe(7));
    navigation.path = "/approvals";
    mounted.rerender(<WebMcpBridge />);
    await waitFor(() => expect(signals).toHaveLength(14));
    expect(signals.slice(0, 7).every((signal) => signal.aborted)).toBe(true);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("disconnected")),
    );
    await active.get("request_status_update")!.execute(
      {
        incident_id: "10000000-0000-4000-8000-000000000001",
        expected_incident_version: 1,
        proposed_status: "resolved",
        rationale: "Synthetic evidence",
      },
      { signal: new AbortController().signal },
    );
    expect(
      await screen.findByRole("button", { name: "Check request result" }),
    ).toBeVisible();
    expect(screen.getByText(/may still complete/)).toBeVisible();
    mounted.unmount();
    expect(active.size).toBe(0);
  });
});

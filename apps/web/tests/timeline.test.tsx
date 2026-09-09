import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Timeline } from "@/components/timeline/timeline";

import { timelineEvent } from "./fixtures";

describe("Timeline", () => {
  it("renders immutable event context and readable payload changes as text", () => {
    render(<Timeline events={[timelineEvent]} />);

    expect(screen.getByText("Incident updated")).toBeInTheDocument();
    expect(
      screen.getByText("human · operator@example.com"),
    ).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("open → investigating")).toBeInTheDocument();
    expect(
      screen.getByText("Customer-facing checkout delay"),
    ).toBeInTheDocument();
    expect(screen.getByText(/2026-09-09 12:42:00 UTC/)).toBeInTheDocument();
  });

  it("has a specific empty state", () => {
    render(<Timeline events={[]} />);

    expect(screen.getByText("No events recorded yet")).toBeInTheDocument();
  });
});

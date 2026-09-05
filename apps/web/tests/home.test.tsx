import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import HomePage from "../app/page";

describe("HomePage", () => {
  it("identifies the Phase 1 product foundation", () => {
    render(<HomePage />);

    expect(
      screen.getByRole("heading", { name: "Incident Ops" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/human-first incident response control plane/i),
    ).toBeInTheDocument();
  });
});

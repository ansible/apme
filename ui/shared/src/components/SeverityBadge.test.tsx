import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SeverityBadge } from "./SeverityBadge";

describe("SeverityBadge", () => {
  it("renders Error label for error level", () => {
    render(<SeverityBadge level="error" />);
    expect(screen.getByText("Error")).toBeInTheDocument();
  });

  it("renders Warning label for warning level", () => {
    render(<SeverityBadge level="warning" />);
    expect(screen.getByText("Warning")).toBeInTheDocument();
  });

  it("renders Hint label for hint level", () => {
    render(<SeverityBadge level="hint" />);
    expect(screen.getByText("Hint")).toBeInTheDocument();
  });
});

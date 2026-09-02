import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusDot } from "@/components/persona/StatusDot";

describe("StatusDot", () => {
  it.each([
    ["online", "Online"],
    ["busy", "Working"],
    ["paused", "Paused"],
    ["offline", "Offline"],
  ] as const)("renders the %s status with class and title matching the API's status string", (status, label) => {
    render(<StatusDot status={status} />);
    const dot = screen.getByTestId("status-dot");

    // The API contract (lib/api.ts) uses "online"/"offline", not the
    // prototype's shorthand "on"/"off" — asserting the class name
    // directly is what catches a StatusDot that quietly reintroduces
    // that mismatch.
    expect(dot).toHaveClass("dot");
    expect(dot).toHaveClass(status);
    expect(dot).not.toHaveClass("on");
    expect(dot).not.toHaveClass("off");
    expect(dot).toHaveAttribute("title", label);
  });
});

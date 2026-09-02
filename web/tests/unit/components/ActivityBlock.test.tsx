import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActivityBlock } from "@/components/conversation/ActivityBlock";

describe("ActivityBlock", () => {
  it("shows a spinner and the label while running, and hides the result", () => {
    render(<ActivityBlock label="Running pytest tests/auth/" done={false} />);

    expect(screen.getByText("Running pytest tests/auth/")).toBeInTheDocument();
    expect(screen.getByTestId("activity-spin")).toBeInTheDocument();
    expect(screen.queryByText("12 passed")).not.toBeInTheDocument();
  });

  it("shows a checkmark and the result once done, and stops spinning", () => {
    render(<ActivityBlock label="Running pytest tests/auth/" done result="12 passed" />);

    expect(screen.queryByTestId("activity-spin")).not.toBeInTheDocument();
    expect(screen.getByText("12 passed")).toBeInTheDocument();
    expect(screen.getByText("Running pytest tests/auth/").closest(".activity-block")).toHaveClass("done");
  });

  it("renders no result text when done but no result was given", () => {
    render(<ActivityBlock label="Cleaning up" done />);

    expect(screen.getByText("Cleaning up")).toBeInTheDocument();
    expect(screen.queryByTestId("activity-spin")).not.toBeInTheDocument();
  });
});

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DiffChip } from "@/components/conversation/DiffChip";

describe("DiffChip", () => {
  it("shows the file count and +/- summary, and links to the conversation's diff screen", () => {
    render(<DiffChip conversationId="grp-auth" taskId="oauth-google" files={3} add={142} del={8} />);

    expect(screen.getByText("3 files changed")).toBeInTheDocument();
    expect(screen.getByText("+142")).toBeInTheDocument();
    expect(screen.getByText("-8")).toBeInTheDocument();

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/conversation/grp-auth/diff/oauth-google");
  });

  it("uses singular \"file\" for a single-file diff", () => {
    render(<DiffChip conversationId="grp-auth" taskId="t2" files={1} add={5} del={0} />);
    expect(screen.getByText("1 file changed")).toBeInTheDocument();
  });
});

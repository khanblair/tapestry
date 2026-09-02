import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { AskQuestion } from "@/lib/api";

// vitest.config.ts doesn't set `test.globals: true`, so Testing Library's
// auto-cleanup never registers itself — see tests/unit/components/PersonaCard.test.tsx
// for the same note.
afterEach(() => cleanup());

// Stub the network boundary only. lib/approvals.ts (the actual thing under
// test, alongside ApprovalCard/ApprovalActions) is NOT mocked — this test
// exercises the real shared store so "approving in one place updates the
// same card everywhere else" is verified against real state, not a fake.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    answerAsk: vi.fn().mockResolvedValue(undefined),
  };
});

const { answerAsk } = await import("@/lib/api");
const { __resetApprovalsForTests } = await import("@/lib/approvals");
const { ApprovalCard } = await import("@/components/approvals/ApprovalCard");

const QUESTION: AskQuestion = {
  id: "appr-1",
  question: "Merge feat/oauth-google → main",
  detail: "Rex wants to merge 3 changed files into main. Tests pass, Vex has reviewed.",
  intent: "approval",
};

beforeEach(() => {
  __resetApprovalsForTests();
  vi.mocked(answerAsk).mockClear();
});

describe("ApprovalCard", () => {
  it("renders the pending state with the question, detail, and both actions", () => {
    const { container } = render(<ApprovalCard conversationId="grp-auth" question={QUESTION} />);
    expect(screen.getByText("Needs your approval")).toBeInTheDocument();
    expect(container.textContent).toContain(QUESTION.question);
    expect(container.textContent).toContain(QUESTION.detail);
    expect(screen.getByRole("button", { name: /Approve/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reject/ })).toBeInTheDocument();
  });

  it("approving calls answerAsk with the shared approve AskAnswer shape and swaps to the resolved card", async () => {
    render(<ApprovalCard conversationId="grp-auth" question={QUESTION} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve/ }));

    expect(await screen.findByText("Approved by you")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reject/ })).not.toBeInTheDocument();
    expect(answerAsk).toHaveBeenCalledWith("grp-auth", [{ id: "appr-1", selected: ["approve"] }]);
  });

  it("rejecting calls answerAsk with the shared reject AskAnswer shape and swaps to the resolved card", async () => {
    render(<ApprovalCard conversationId="grp-auth" question={QUESTION} />);
    fireEvent.click(screen.getByRole("button", { name: /Reject/ }));

    expect(await screen.findByText("Changes requested")).toBeInTheDocument();
    expect(answerAsk).toHaveBeenCalledWith("grp-auth", [{ id: "appr-1", selected: ["reject"] }]);
  });

  it("shares approval state across every mounted card for the same question id — approving in one updates the others", async () => {
    render(
      <>
        <ApprovalCard conversationId="grp-auth" question={QUESTION} />
        <ApprovalCard conversationId="grp-auth" question={QUESTION} />
      </>
    );
    const approveButtons = screen.getAllByRole("button", { name: /Approve/ });
    expect(approveButtons).toHaveLength(2);

    fireEvent.click(approveButtons[0]);

    const resolvedHeaders = await screen.findAllByText("Approved by you");
    expect(resolvedHeaders).toHaveLength(2);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { DiffDetail } from "@/lib/api";

afterEach(() => cleanup());

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const getDiffDetail = vi.fn();
const getApprovalForDiff = vi.fn();
vi.mock("@/lib/safeApi", () => ({
  getDiffDetail: (...args: unknown[]) => getDiffDetail(...args),
  getApprovalForDiff: (...args: unknown[]) => getApprovalForDiff(...args),
}));

const { DiffScreenView } = await import("@/app/conversation/[id]/diff/[taskId]/DiffScreenView");

const DIFF: DiffDetail = {
  taskId: "oauth-google",
  title: "feat/oauth-google",
  fileCount: 1,
  additions: 5,
  deletions: 1,
  files: [{ name: "oauth/google.py", lines: [{ type: "add", lineNumber: 1, content: "x = 1" }] }],
};

beforeEach(() => {
  push.mockClear();
  getDiffDetail.mockReset();
  getApprovalForDiff.mockReset();
});

describe("DiffScreenView", () => {
  it("fetches both the diff and the approval scoped to the given conversationId, not just the taskId", async () => {
    getDiffDetail.mockResolvedValue(DIFF);
    getApprovalForDiff.mockResolvedValue(null);

    render(<DiffScreenView conversationId="grp-auth" taskId="oauth-google" />);

    await screen.findByText("No pending approval is linked to this diff.");
    expect(getDiffDetail).toHaveBeenCalledWith("grp-auth", "oauth-google");
    expect(getApprovalForDiff).toHaveBeenCalledWith("grp-auth", "oauth-google");
  });

  it("shows Approve merge/Request changes actions when getApprovalForDiff resolves a linked approval", async () => {
    getDiffDetail.mockResolvedValue(DIFF);
    getApprovalForDiff.mockResolvedValue({
      conversationId: "grp-auth",
      question: { id: "appr-1", question: "Merge feat/oauth-google → main" },
    });

    render(<DiffScreenView conversationId="grp-auth" taskId="oauth-google" />);

    expect(await screen.findByRole("button", { name: "Approve merge" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request changes" })).toBeInTheDocument();
  });

  it("shows a not-found hint when getDiffDetail resolves null (a confirmed 404)", async () => {
    getDiffDetail.mockResolvedValue(null);
    getApprovalForDiff.mockResolvedValue(null);

    render(<DiffScreenView conversationId="grp-auth" taskId="missing-task" />);

    expect(await screen.findByText("Diff not found.")).toBeInTheDocument();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DiffDetail, Message, PendingApproval } from "@/lib/api";

// Stub the network boundary only (lib/api.ts's request()-based fetchers) so
// these tests exercise lib/safeApi.ts's own try-real-then-fallback logic
// against controlled resolve/reject outcomes, not a real fetch.
const apiGetDiffDetail = vi.fn();
const apiGetPendingApprovals = vi.fn();
const apiGetMessages = vi.fn();
const apiGetConversations = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getDiffDetail: (...args: unknown[]) => apiGetDiffDetail(...args),
    getPendingApprovals: (...args: unknown[]) => apiGetPendingApprovals(...args),
    getMessages: (...args: unknown[]) => apiGetMessages(...args),
    getConversations: (...args: unknown[]) => apiGetConversations(...args),
  };
});

const {
  getDiffDetail,
  getApprovalForDiff,
  getPendingApprovals,
} = await import("@/lib/safeApi");
const { MOCK_DIFFS } = await import("@/lib/mockData");

afterEach(() => {
  vi.clearAllMocks();
});

beforeEach(() => {
  apiGetDiffDetail.mockReset();
  apiGetPendingApprovals.mockReset();
  apiGetMessages.mockReset();
  apiGetConversations.mockReset();
});

describe("safeApi getDiffDetail", () => {
  it("returns the real backend's diff detail when the call succeeds", async () => {
    const real: DiffDetail = {
      taskId: "t1",
      title: "real diff",
      fileCount: 1,
      additions: 3,
      deletions: 0,
      files: [{ name: "a.py", lines: [{ type: "add", lineNumber: 1, content: "x = 1" }] }],
    };
    apiGetDiffDetail.mockResolvedValue(real);

    await expect(getDiffDetail("grp-auth", "t1")).resolves.toEqual(real);
    expect(apiGetDiffDetail).toHaveBeenCalledWith("grp-auth", "t1");
  });

  it("stays null on a clean, confirmed 404 rather than silently swapping in unrelated mock data", async () => {
    apiGetDiffDetail.mockResolvedValue(null);

    await expect(getDiffDetail("grp-auth", "oauth-google")).resolves.toBeNull();
  });

  it("falls back to lib/mockData.ts's MOCK_DIFFS only when the call throws", async () => {
    apiGetDiffDetail.mockRejectedValue(new Error("network error"));

    await expect(getDiffDetail("grp-auth", "oauth-google")).resolves.toEqual(MOCK_DIFFS["oauth-google"]);
  });
});

describe("safeApi getApprovalForDiff", () => {
  it("finds the message whose approval.relatedTaskId matches, scoped to just the given conversation", async () => {
    const messages: Message[] = [
      { id: "m1", conversationId: "grp-auth", actor: "rex", text: "on it", timestamp: "t", eventType: "message" },
      {
        id: "m2",
        conversationId: "grp-auth",
        actor: "rex",
        text: "ready to merge",
        timestamp: "t",
        eventType: "message",
        approval: { id: "appr-1", question: "Merge feat/oauth-google → main", relatedTaskId: "oauth-google" },
      },
    ];
    apiGetMessages.mockResolvedValue(messages);

    const result = await getApprovalForDiff("grp-auth", "oauth-google");

    expect(result).toEqual({ conversationId: "grp-auth", question: messages[1].approval });
    expect(apiGetMessages).toHaveBeenCalledWith("grp-auth");
    // The precise lookup only ever reads the one conversation it's given —
    // no cross-conversation scan the old heuristic used to need.
    expect(apiGetConversations).not.toHaveBeenCalled();
  });

  it("returns null when no message's approval.relatedTaskId matches the given taskId", async () => {
    apiGetMessages.mockResolvedValue([
      {
        id: "m2",
        conversationId: "grp-auth",
        actor: "rex",
        text: "ready to merge",
        timestamp: "t",
        eventType: "message",
        approval: { id: "appr-1", question: "Merge", relatedTaskId: "some-other-task" },
      },
    ]);

    await expect(getApprovalForDiff("grp-auth", "oauth-google")).resolves.toBeNull();
  });
});

describe("safeApi getPendingApprovals", () => {
  it("returns the real backend's pending list when the call succeeds", async () => {
    const real: PendingApproval[] = [
      { conversationId: "grp-auth", conversationLabel: "#auth-rework", question: { id: "appr-1", question: "Merge" } },
    ];
    apiGetPendingApprovals.mockResolvedValue(real);

    await expect(getPendingApprovals()).resolves.toEqual(real);
    expect(apiGetConversations).not.toHaveBeenCalled();
  });

  it("falls back to scanning every conversation's messages when the call throws", async () => {
    apiGetPendingApprovals.mockRejectedValue(new Error("network error"));
    apiGetConversations.mockResolvedValue([
      { id: "grp-auth", kind: "group", name: "#auth-rework", personaIds: ["rex"], updatedAt: "t" },
    ]);
    apiGetMessages.mockResolvedValue([
      {
        id: "m1",
        conversationId: "grp-auth",
        actor: "rex",
        text: "ready",
        timestamp: "t",
        eventType: "message",
        approval: { id: "appr-1", question: "Merge" },
      },
    ]);

    const result = await getPendingApprovals();

    expect(result).toEqual([
      { conversationId: "grp-auth", conversationLabel: "#auth-rework", question: { id: "appr-1", question: "Merge" } },
    ]);
  });
});

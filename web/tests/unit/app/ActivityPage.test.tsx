import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ActivityFeed } from "@/lib/api";

afterEach(() => cleanup());

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const getPendingApprovals = vi.fn();
const safeGetActivity = vi.fn();
const pauseAllAgents = vi.fn();
vi.mock("@/lib/safeApi", () => ({
  getPendingApprovals: (...args: unknown[]) => getPendingApprovals(...args),
  safeGetActivity: (...args: unknown[]) => safeGetActivity(...args),
  pauseAllAgents: (...args: unknown[]) => pauseAllAgents(...args),
}));

const { default: ActivityPage } = await import("@/app/activity/page");

beforeEach(() => {
  push.mockClear();
  getPendingApprovals.mockReset().mockResolvedValue([]);
  safeGetActivity.mockReset();
  pauseAllAgents.mockReset();
});

describe("ActivityPage", () => {
  it("renders 'Running now' and 'Recent' from GET /api/activity (via safeGetActivity), not hardcoded copy", async () => {
    const feed: ActivityFeed = {
      running: [
        {
          conversationId: "grp-auth",
          conversationLabel: "#auth-rework",
          actor: "Rex",
          label: "running pytest tests/auth/",
          timestamp: new Date().toISOString(),
        },
      ],
      recent: [
        {
          conversationId: "grp-auth",
          conversationLabel: "#auth-rework",
          actor: "Ada",
          label: "proposed OAuth architecture",
          timestamp: new Date(Date.now() - 30 * 60_000).toISOString(),
        },
      ],
    };
    safeGetActivity.mockResolvedValue(feed);

    render(<ActivityPage />);

    expect(await screen.findByText("Rex · running pytest tests/auth/")).toBeInTheDocument();
    expect(await screen.findByText("Ada · proposed OAuth architecture · 30m ago")).toBeInTheDocument();
  });

  it("shows the empty-hint when nothing is running, matching the 'Needs your input' empty-state pattern", async () => {
    safeGetActivity.mockResolvedValue({ running: [], recent: [] });

    render(<ActivityPage />);

    expect(await screen.findByText("Nothing running right now.")).toBeInTheDocument();
  });

  it("shows nothing for 'Running now' before the fetch resolves (no stale hardcoded block)", () => {
    safeGetActivity.mockReturnValue(new Promise(() => {})); // never resolves

    render(<ActivityPage />);

    expect(screen.queryByText(/running pytest/)).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing running right now.")).not.toBeInTheDocument();
  });
});

import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PlatformsPanel } from "@/components/settings/PlatformsPanel";
import { ModelProvidersPanel } from "@/components/settings/ModelProvidersPanel";
import { ToolsAndMcpPanel } from "@/components/settings/ToolsAndMcpPanel";
import type { SystemStatus } from "@/lib/api";

afterEach(() => cleanup());

const STATUS: SystemStatus = {
  platforms: [
    { name: "Discord", detail: "Connected as @tapestry-bot", connected: true, alwaysOn: false },
    { name: "Telegram", detail: "Not connected", connected: false, alwaysOn: false },
    { name: "Web", detail: "Always on", connected: true, alwaysOn: true },
  ],
  providers: [
    { name: "Anthropic", connected: true },
    { name: "Qwen", connected: false },
  ],
  metamcp: { running: true, serverCount: 4 },
  mcpServers: [
    { name: "filesystem", connected: true },
    { name: "git", connected: false },
  ],
};

describe("PlatformsPanel", () => {
  it("shows a loading state before status is fetched", () => {
    render(<PlatformsPanel status={null} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders each platform row from the fetched status, with a static chip (not a toggle) for the always-on one", () => {
    render(<PlatformsPanel status={STATUS} />);

    expect(screen.getByText("Discord")).toBeInTheDocument();
    expect(screen.getByText("Connected as @tapestry-bot")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Discord connection" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "Telegram connection" })).toHaveAttribute("aria-checked", "false");

    expect(screen.queryByRole("switch", { name: "Web connection" })).not.toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("toggling one row's switch doesn't affect the others (still client-only UI state)", () => {
    render(<PlatformsPanel status={STATUS} />);

    fireEvent.click(screen.getByRole("switch", { name: "Discord connection" }));

    expect(screen.getByRole("switch", { name: "Discord connection" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: "Telegram connection" })).toHaveAttribute("aria-checked", "false");
  });
});

describe("ModelProvidersPanel", () => {
  it("shows a loading state before status is fetched", () => {
    render(<ModelProvidersPanel status={null} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders each provider's connected/not-connected chip from the fetched status", () => {
    render(<ModelProvidersPanel status={STATUS} />);

    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Qwen")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
  });
});

describe("ToolsAndMcpPanel", () => {
  it("shows a loading state before status is fetched", () => {
    render(<ToolsAndMcpPanel status={null} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders metamcp's own running state/serverCount separately from the mcpServers row list", () => {
    render(<ToolsAndMcpPanel status={STATUS} />);

    expect(screen.getByText("Running")).toBeInTheDocument();
    // metamcp.serverCount (4) here, not mcpServers.length (2) -- the fixture
    // deliberately sets these to different numbers so a shortcut like
    // `mcpServers.length` would fail this assertion.
    expect(screen.getByText(/4 servers connected/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "filesystem MCP server" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "git MCP server" })).toHaveAttribute("aria-checked", "false");
  });
});

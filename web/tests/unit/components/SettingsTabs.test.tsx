import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import type { Persona, SystemStatus } from "@/lib/api";

afterEach(() => cleanup());

// next/link needs App Router context to do its real work (prefetching etc.);
// for a plain rendering test a bare <a> is all we need — same stub as
// tests/unit/components/PersonaCard.test.tsx.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const safeGetPersonas = vi.fn();
const safeGetStatus = vi.fn();
vi.mock("@/lib/safeApi", () => ({
  safeGetPersonas: (...args: unknown[]) => safeGetPersonas(...args),
  safeGetStatus: (...args: unknown[]) => safeGetStatus(...args),
}));

const { SettingsTabs } = await import("@/components/settings/SettingsTabs");

const STATUS: SystemStatus = {
  platforms: [{ name: "Discord", detail: "Connected as @tapestry-bot", connected: true, alwaysOn: false }],
  providers: [],
  metamcp: { running: true, serverCount: 0 },
  mcpServers: [],
};

beforeEach(() => {
  safeGetPersonas.mockReset().mockResolvedValue([] as Persona[]);
  safeGetStatus.mockReset().mockResolvedValue(STATUS);
});

describe("SettingsTabs", () => {
  it("fetches status once at this level and passes it down to the active panel, rather than each panel fetching its own", async () => {
    render(<SettingsTabs />);

    // Platforms is the default active tab; before the fetch resolves it
    // shows the panel's own loading state, not a hardcoded row.
    expect(screen.getByText("Loading…")).toBeInTheDocument();

    expect(await screen.findByText("Discord")).toBeInTheDocument();
    expect(safeGetStatus).toHaveBeenCalledTimes(1);
  });
});

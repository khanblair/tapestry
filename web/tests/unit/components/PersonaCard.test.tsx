import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Persona } from "@/lib/api";

// vitest.config.ts doesn't set `test.globals: true`, so Testing Library's
// auto-cleanup (which detects a *global* afterEach) never registers itself.
// Without this, DOM from each render() in this file piles up across tests.
afterEach(() => cleanup());

// next/link needs App Router context to do its real work (prefetching etc.);
// for a plain rendering test a bare <a> is all we need.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// PersonaAvatar is owned by the sibling agent building the app shell; stub it
// so this test exercises PersonaCard's own row/link/text logic, not the
// avatar's internals.
vi.mock("@/components/persona/PersonaAvatar", () => ({
  PersonaAvatar: ({ persona }: { persona: Persona }) => (
    <div data-testid="avatar">{persona.name[0]}</div>
  ),
}));

const { PersonaCard } = await import("@/components/persona/PersonaCard");

const REX: Persona = {
  id: "rex",
  name: "Rex",
  role: "Developer",
  model: "DeepSeek V3.2",
  status: "busy",
  color: "#8B5CF6",
};

describe("PersonaCard", () => {
  it("renders the persona's name, role, and model", () => {
    render(<PersonaCard persona={REX} />);
    expect(screen.getByText("Rex")).toBeInTheDocument();
    expect(screen.getByText(/Developer/)).toBeInTheDocument();
    expect(screen.getByText(/DeepSeek V3.2/)).toBeInTheDocument();
  });

  it("links to the persona's edit route by default", () => {
    render(<PersonaCard persona={REX} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/personas/rex");
  });

  it("respects a custom href override", () => {
    render(<PersonaCard persona={REX} href="/custom/rex" />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/custom/rex");
  });

  it("renders the shared PersonaAvatar for the row's avatar", () => {
    render(<PersonaCard persona={REX} />);
    expect(screen.getByTestId("avatar")).toBeInTheDocument();
  });
});

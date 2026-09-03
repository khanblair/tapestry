import { afterEach, beforeEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import type { Conversation, Persona } from "@/lib/api";
import { RosterList } from "@/components/roster/RosterList";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

beforeEach(() => {
  window.localStorage.clear();
});

const REX: Persona = {
  id: "rex",
  name: "Rex",
  role: "Developer",
  model: "DeepSeek V3.2",
  status: "online",
  color: "#8B5CF6",
};

const CONVERSATIONS: Conversation[] = [
  {
    id: "dm-rex",
    kind: "dm",
    personaIds: ["rex"],
    updatedAt: new Date().toISOString(),
    mode: "manual",
    model: "DeepSeek V3.2",
    archived: false,
  },
];

// Regression coverage for a live-reported bug: an early `if (collapsed)
// return <button/>` used to unmount .pane-roster ENTIRELY whenever
// `collapsed` was true, regardless of viewport. Since `collapsed` persists
// in localStorage across navigation, a value saved on a wide screen (where
// collapsing makes sense) meant a narrower view showed NO roster panel at
// all -- and the always-fixed-position expand button then landed on top of
// the conversation pane's own back-arrow icon, both drawing in the same
// top-left corner. The fix: always render .pane-roster; collapsing (and
// the expand button) are pure CSS effects gated to 900px+ in globals.css,
// so they're structurally incapable of blanking the panel or colliding
// with anything below that breakpoint.
describe("RosterList collapse state", () => {
  it("always renders .pane-roster, even when collapsed=true was persisted from a previous session", () => {
    window.localStorage.setItem("tapestry:sidebarCollapsed", "true");
    const { container } = render(<RosterList personas={[REX]} conversations={CONVERSATIONS} />);

    expect(container.querySelector(".pane-roster")).not.toBeNull();
  });

  it("applies the .collapsed class when collapsed, for the 900px+-only CSS effect to key off", () => {
    window.localStorage.setItem("tapestry:sidebarCollapsed", "true");
    const { container } = render(<RosterList personas={[REX]} conversations={CONVERSATIONS} />);

    expect(container.querySelector(".pane-roster")).toHaveClass("collapsed");
  });

  it("does not apply .collapsed by default", () => {
    const { container } = render(<RosterList personas={[REX]} conversations={CONVERSATIONS} />);

    expect(container.querySelector(".pane-roster")).not.toHaveClass("collapsed");
  });

  it("marks the floating expand button visible only when collapsed (CSS still gates it to 900px+)", () => {
    window.localStorage.setItem("tapestry:sidebarCollapsed", "true");
    const { container } = render(<RosterList personas={[REX]} conversations={CONVERSATIONS} />);

    expect(container.querySelector(".sidebar-expand-btn")).toHaveClass("visible");
  });

  it("does not mark the expand button visible by default", () => {
    const { container } = render(<RosterList personas={[REX]} conversations={CONVERSATIONS} />);

    expect(container.querySelector(".sidebar-expand-btn")).not.toHaveClass("visible");
  });
});

describe("RosterList archived conversations", () => {
  it("segregates an archived conversation out of the Direct Messages section", () => {
    const archived: Conversation[] = [{ ...CONVERSATIONS[0], archived: true }];
    const { getByText, queryByText } = render(
      <RosterList personas={[REX]} conversations={archived} />
    );

    expect(getByText(/Archived \(1\)/)).toBeInTheDocument();
    expect(queryByText("No direct messages yet.")).toBeInTheDocument();
  });
});

import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import type { Persona } from "@/lib/api";

/**
 * Regression test for the exact bug the prototype shipped once (see
 * project_structure.md: "persona-edit close not leaking `editingPersona`
 * into the next screen" / the prototype's `state.editingPersona` living at
 * module scope instead of being tied to a component's lifecycle).
 *
 * This does NOT just assert "React unmounts clear state" -- that would be
 * testing React, not this codebase. It asserts this component's own
 * contract: closing navigates away via the router (not just local UI
 * state, which is the literal shape of the prototype's bug -- state kept in
 * a variable that outlives the screen instead of being scoped to it), and
 * a fresh mount for a persona never shows a previous session's leftover
 * form state. The last test below ("does not carry a failed save's error
 * onto a different persona") is the one that actually depends on
 * `key={personaId}` in page.tsx -- see its comment for why the more
 * obvious "does the draft leak" check does not, in this implementation.
 */

// vitest.config.ts doesn't set `test.globals: true`, so Testing Library's
// auto-cleanup (which detects a *global* afterEach) never registers itself.
// Without this, DOM from each render() in this file piles up across tests,
// which is exactly the kind of cross-test bleed this file's tests are
// supposed to be ruling out in the component itself -- so it matters doubly
// here that the *test harness* isn't the thing leaking state.
afterEach(() => cleanup());

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const getPersonaById = vi.fn();
vi.mock("@/lib/safeApi", () => ({
  getPersonaById: (...args: unknown[]) => getPersonaById(...args),
}));

const createPersona = vi.fn();
const updatePersona = vi.fn();
vi.mock("@/lib/api", () => ({
  createPersona: (...args: unknown[]) => createPersona(...args),
  updatePersona: (...args: unknown[]) => updatePersona(...args),
}));

// Import the actual route file, not a hand-rolled stand-in -- this is what
// makes the id-switch test below cover page.tsx's `key={personaId}` line
// for real, rather than only testing a copy of it.
const { default: PersonaEditPage } = await import("@/app/personas/[personaId]/page");

async function renderEdit(personaId: string) {
  const element = (await PersonaEditPage({
    params: Promise.resolve({ personaId }),
  })) as ReactElement;
  return render(element);
}

async function rerenderEdit(rerender: (ui: ReactElement) => void, personaId: string) {
  const element = (await PersonaEditPage({
    params: Promise.resolve({ personaId }),
  })) as ReactElement;
  rerender(element);
}

const ADA: Persona = {
  id: "ada",
  name: "Ada",
  role: "Architect",
  model: "Claude Opus 4.8",
  status: "online",
  color: "#3B82F6",
  bio: "Plans system design.",
  tools: ["File read"],
};
const REX: Persona = {
  id: "rex",
  name: "Rex",
  role: "Developer",
  model: "DeepSeek V3.2",
  status: "busy",
  color: "#8B5CF6",
  bio: "Implements features.",
  tools: ["File edit", "Shell exec"],
};
const ROSTER = [ADA, REX];

beforeEach(() => {
  push.mockClear();
  createPersona.mockClear();
  updatePersona.mockClear();
  getPersonaById.mockReset();
  getPersonaById.mockImplementation((id: string) =>
    Promise.resolve(ROSTER.find((p) => p.id === id) ?? null)
  );
});

describe("persona edit form state clearing", () => {
  it("navigates back to the list (not just a local close) when the × is clicked", async () => {
    await renderEdit("ada");
    await screen.findByDisplayValue("Ada");

    fireEvent.change(screen.getByDisplayValue("Ada"), { target: { value: "Ada (edited)" } });
    expect(screen.getByDisplayValue("Ada (edited)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(push).toHaveBeenCalledWith("/personas");
  });

  it("does not leak a previous persona's edited draft when the route id changes", async () => {
    const { rerender } = await renderEdit("ada");
    await screen.findByDisplayValue("Ada");

    fireEvent.change(screen.getByDisplayValue("Ada"), { target: { value: "Ada (edited)" } });
    expect(screen.getByDisplayValue("Ada (edited)")).toBeInTheDocument();

    // Same transition App Router performs navigating /personas/ada ->
    // /personas/rex: same route file, changed dynamic segment. Goes through
    // the real page.tsx (see renderEdit/rerenderEdit above).
    //
    // Note this assertion passes with or without `key={personaId}` in
    // page.tsx: the fetch effect's dependency array is `[personaId,
    // isNew]`, so it re-runs and overwrites `draft`/`existing` regardless
    // of whether the component instance is fresh. It's the "does not carry
    // a failed save's error onto a different persona" test below that
    // actually depends on the key -- `saveError` is state the effect never
    // touches, so it's the one thing that genuinely leaks without it.
    // Kept here anyway as a straightforward, still-true regression check on
    // the draft itself.
    await rerenderEdit(rerender, "rex");

    await screen.findByDisplayValue("Rex");
    expect(screen.queryByDisplayValue("Ada")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Ada (edited)")).not.toBeInTheDocument();
  });

  it("keeps no state in anything that outlives the route (module scope, a shared store) -- a fresh visit is always clean", async () => {
    await renderEdit("ada");
    await screen.findByDisplayValue("Ada");

    fireEvent.change(screen.getByDisplayValue("Ada"), { target: { value: "Ada (edited)" } });

    // Simulate actually leaving the edit route (the component unmounts in
    // the real app when navigating to /personas) and coming back to the
    // same id later.
    cleanup();
    await renderEdit("ada");

    await screen.findByDisplayValue("Ada");
    expect(screen.queryByDisplayValue("Ada (edited)")).not.toBeInTheDocument();
  });

  it("saves via updatePersona with the standing-instructions/tools patch, then returns to the list", async () => {
    updatePersona.mockResolvedValue({ ...ADA });
    await renderEdit("ada");
    await screen.findByDisplayValue("Ada");

    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await waitFor(() =>
      expect(updatePersona).toHaveBeenCalledWith(
        "ada",
        expect.objectContaining({ systemPrompt: expect.any(String), tools: expect.any(Array) })
      )
    );
    expect(push).toHaveBeenCalledWith("/personas");
  });

  it("creates via createPersona for the 'new' sentinel id and returns to the list", async () => {
    createPersona.mockResolvedValue({ ...ADA, id: "new-id" });
    await renderEdit("new");
    await screen.findByRole("heading", { name: /new persona/i });

    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "Zed" } });
    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await waitFor(() => expect(createPersona).toHaveBeenCalled());
    expect(push).toHaveBeenCalledWith("/personas");
  });

  it("keeps the draft and shows an error, without navigating away, when the save fails", async () => {
    updatePersona.mockRejectedValue(new Error("network error"));
    await renderEdit("ada");
    await screen.findByDisplayValue("Ada");

    fireEvent.change(screen.getByDisplayValue("Ada"), { target: { value: "Ada (edited)" } });
    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await screen.findByText(/couldn.t save/i);
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue("Ada (edited)")).toBeInTheDocument();
  });

  it("does not carry a failed save's error onto a different persona", async () => {
    // This is the assertion that actually depends on `key={personaId}` in
    // page.tsx (verified by temporarily removing it and re-running this
    // file -- this test alone failed, all others still passed). `draft` and
    // `existing` can't leak across an id switch either way: the fetch
    // effect's dependency array is `[personaId, isNew]`, so it re-runs and
    // overwrites both regardless of whether the component instance is
    // fresh. `saveError`, however, is state the effect never touches -- so
    // without the key, a failed save on Ada would still show "Couldn't
    // save" under Rex's freshly-loaded form.
    updatePersona.mockRejectedValue(new Error("network error"));
    const { rerender } = await renderEdit("ada");
    await screen.findByDisplayValue("Ada");

    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
    await screen.findByText(/couldn.t save/i);

    await rerenderEdit(rerender, "rex");
    await screen.findByDisplayValue("Rex");
    expect(screen.queryByText(/couldn.t save/i)).not.toBeInTheDocument();
  });
});

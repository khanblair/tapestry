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
const safeGetStatus = vi.fn();
vi.mock("@/lib/safeApi", () => ({
  getPersonaById: (...args: unknown[]) => getPersonaById(...args),
  safeGetStatus: (...args: unknown[]) => safeGetStatus(...args),
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

// Real TOOL_REGISTRY/MODEL values, matching what the backend actually
// stores (personas/*.yaml) -- NOT display labels. See personaDetails.ts's
// TOOL_OPTIONS/MODEL_OPTIONS docstrings for the bug this distinction fixes.
const ADA: Persona = {
  id: "ada",
  name: "Ada",
  role: "Architect",
  model: "claude-opus-4-6",
  status: "online",
  color: "#3B82F6",
  systemPrompt: "Plans system design.",
  tools: ["file_editor_read"],
};
const REX: Persona = {
  id: "rex",
  name: "Rex",
  role: "Developer",
  model: "deepseek/deepseek-chat",
  status: "busy",
  color: "#8B5CF6",
  systemPrompt: "Implements features.",
  tools: ["file_editor", "terminal"],
  mcp: ["filesystem", "terminal"],
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
  safeGetStatus.mockReset();
  safeGetStatus.mockResolvedValue({
    platforms: [],
    providers: [],
    metamcp: { running: true, serverCount: 4 },
    mcpServers: [
      { name: "filesystem", connected: true },
      { name: "git", connected: true },
      { name: "terminal", connected: true },
      { name: "browser", connected: true },
    ],
  });
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

describe("persona edit form -- tapestry_modes_models_personas_spec.md §3 fields", () => {
  it("does not silently drop the mcp field -- pre-checks the persona's real mcp servers and includes them, unmodified, in the update payload", async () => {
    // Direct regression test for the exact gap the spec called out: `mcp`
    // was already a real Persona/PersonaDraft field (lib/api.ts) with no
    // form control for it at all, so an update payload built from the old
    // DraftState always sent nothing for it -- silently discarding whatever
    // MCP servers a persona had, even server-side, the moment someone saved
    // an unrelated edit through this form. REX's fixture above sets
    // `mcp: ["filesystem", "terminal"]`.
    updatePersona.mockResolvedValue({ ...REX });
    await renderEdit("rex");
    await screen.findByDisplayValue("Rex");

    expect(await screen.findByRole("checkbox", { name: "filesystem" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "terminal" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "git" })).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await waitFor(() =>
      expect(updatePersona).toHaveBeenCalledWith(
        "rex",
        expect.objectContaining({ mcp: expect.arrayContaining(["filesystem", "terminal"]) })
      )
    );
    const payload = updatePersona.mock.calls[0][1] as { mcp: string[] };
    expect(payload.mcp).toHaveLength(2);
  });

  it("pre-checks an existing persona's real tools and preserves them on save (real TOOL_REGISTRY ids, not display labels)", async () => {
    // Regression test for a real, severe bug: TOOL_OPTIONS used to be a
    // flat string[] of display labels ("File edit", "Shell exec", ...)
    // used directly as both the checkbox value AND the `tools` payload --
    // since REX's REAL tools are `["file_editor", "terminal"]` (real
    // TOOL_REGISTRY keys, per backend/tapestry/graph/build.py), every
    // checkbox rendered UNCHECKED regardless of the persona's actual
    // permissions, and saving without manually re-checking everything
    // would have silently stripped them -- a persona created through this
    // form at all could never successfully use any tool, since none of
    // the sent strings matched a real registry key.
    updatePersona.mockResolvedValue({ ...REX });
    await renderEdit("rex");
    await screen.findByDisplayValue("Rex");

    expect(screen.getByRole("checkbox", { name: "File edit" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Shell exec" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "File read" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Git" })).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await waitFor(() => expect(updatePersona).toHaveBeenCalled());
    const payload = updatePersona.mock.calls[0][1] as { tools: string[] };
    expect(payload.tools.sort()).toEqual(["file_editor", "terminal"]);
  });

  it("round-trips all six new fields (mcp, fallbackModels, guardianModel, reasoningEffort, defaultMode, maxTurns, maxDelegationDepth) through create", async () => {
    createPersona.mockResolvedValue({ ...ADA, id: "new-id" });
    await renderEdit("new");
    await screen.findByRole("heading", { name: /new persona/i });

    fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "Zed" } });
    fireEvent.change(screen.getByLabelText(/^role$/i), { target: { value: "Tester" } });

    // mcp -- real server names come from safeGetStatus() (mocked in
    // beforeEach above), same source ToolsAndMcpPanel uses.
    fireEvent.click(await screen.findByRole("checkbox", { name: "filesystem" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "git" }));

    // fallbackModels -- excludes the current primary model, which for a
    // fresh EMPTY_DRAFT is MODEL_OPTIONS[0] ("Claude Opus 4.6"), so neither
    // of these two checkboxes collide with it. Checkbox accessible names
    // are the display LABEL; the value sent to the backend is the real
    // model id (see the payload assertion below).
    fireEvent.click(screen.getByRole("checkbox", { name: "Claude Sonnet 5" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "DeepSeek Chat" }));

    fireEvent.change(screen.getByLabelText(/guardian model/i), { target: { value: "gemini/gemini-3-pro" } });
    fireEvent.change(screen.getByLabelText(/reasoning effort/i), { target: { value: "high" } });
    fireEvent.change(screen.getByLabelText(/default mode/i), { target: { value: "auto" } });
    fireEvent.change(screen.getByLabelText(/max turns/i), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText(/max delegation depth/i), { target: { value: "2" } });

    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await waitFor(() => expect(createPersona).toHaveBeenCalled());
    expect(createPersona).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Zed",
        role: "Tester",
        guardianModel: "gemini/gemini-3-pro",
        reasoningEffort: "high",
        defaultMode: "auto",
        maxTurns: 7,
        maxDelegationDepth: 2,
      })
    );
    const payload = createPersona.mock.calls[0][0] as {
      mcp: string[];
      fallbackModels: string[];
    };
    expect(payload.mcp.sort()).toEqual(["filesystem", "git"]);
    expect(payload.fallbackModels.sort()).toEqual(["claude-sonnet-5", "deepseek/deepseek-chat"]);
    expect(push).toHaveBeenCalledWith("/personas");
  });

  it("omits guardianModel/reasoningEffort/maxTurns/maxDelegationDepth when left unset, rather than sending empty strings or zeros", async () => {
    createPersona.mockResolvedValue({ ...ADA, id: "new-id" });
    await renderEdit("new");
    await screen.findByRole("heading", { name: /new persona/i });

    fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "Zed" } });
    fireEvent.click(screen.getByRole("button", { name: /save persona/i }));

    await waitFor(() => expect(createPersona).toHaveBeenCalled());
    const payload = createPersona.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.guardianModel).toBeUndefined();
    expect(payload.reasoningEffort).toBeUndefined();
    expect(payload.maxTurns).toBeUndefined();
    expect(payload.maxDelegationDepth).toBeUndefined();
    // defaultMode always has a concrete value ("manual" default), unlike
    // the other four -- it's not nullable on Persona.
    expect(payload.defaultMode).toBe("manual");
  });
});

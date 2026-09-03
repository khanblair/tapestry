import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Conversation, ConversationEvent, Persona } from "@/lib/api";

// Integration coverage for the one piece of derivation logic
// ConversationView itself owns (leadPersonaId = conversation.personaIds[0])
// and the header-model wiring — the isolated ModeSwitcher/ModelSwitcher
// tests exercise those components directly with a literal personaId prop,
// so they can't catch ConversationView passing the wrong one, or the topbar
// showing two different models for "what model is this conversation
// running?" (primaryPersona.model vs conversation.model).

afterEach(() => cleanup());

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.ComponentProps<"a">) => (
    <a href={typeof href === "string" ? href : ""} {...rest}>
      {children}
    </a>
  ),
}));

// ConversationMenu (the topbar's 3-dot dropdown) calls useRouter() to
// navigate away after a delete, and .refresh() after an archive toggle so
// the separately-server-fetched RosterList picks up the change without a
// full navigation -- same mock precedent as PersonaEditForm.test.tsx.
const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

const subscribeToConversation = vi.fn(
  (_conversationId: string, _onEvent: (event: ConversationEvent) => void) => () => {}
);
const setConversationMode = vi.fn();
const setConversationModel = vi.fn();
const setConversationContext = vi.fn();
const setConversationArchived = vi.fn();
const deleteConversation = vi.fn();
const sendMessage = vi.fn();
const stopConversation = vi.fn();
const editMessage = vi.fn();
const deleteMessage = vi.fn();
const reactToMessage = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    subscribeToConversation: (...args: unknown[]) =>
      (subscribeToConversation as unknown as (...a: unknown[]) => () => void)(...args),
    setConversationMode: (...args: unknown[]) => setConversationMode(...args),
    setConversationModel: (...args: unknown[]) => setConversationModel(...args),
    setConversationContext: (...args: unknown[]) => setConversationContext(...args),
    setConversationArchived: (...args: unknown[]) => setConversationArchived(...args),
    deleteConversation: (...args: unknown[]) => deleteConversation(...args),
    sendMessage: (...args: unknown[]) => sendMessage(...args),
    stopConversation: (...args: unknown[]) => stopConversation(...args),
    editMessage: (...args: unknown[]) => editMessage(...args),
    deleteMessage: (...args: unknown[]) => deleteMessage(...args),
    reactToMessage: (...args: unknown[]) => reactToMessage(...args),
  };
});

const { ConversationView } = await import("@/components/conversation/ConversationView");

const REX: Persona = {
  id: "rex",
  name: "Rex",
  role: "Developer",
  model: "DeepSeek V3.2",
  status: "busy",
  color: "#8B5CF6",
};
const ADA: Persona = {
  id: "ada",
  name: "Ada",
  role: "Architect",
  model: "Claude Opus 4.8",
  status: "online",
  color: "#3B82F6",
};
const VEX: Persona = {
  id: "vex",
  name: "Vex",
  role: "Security & QA",
  model: "Claude Sonnet 5",
  status: "online",
  color: "#F43F5E",
};

// Mode/Model switchers moved out of the topbar into the conversation
// settings panel (opened from the 3-dot ConversationMenu's "Settings"
// item) -- see ConversationSettingsPanel.tsx. Every test below opens that
// panel first, same as a real user would, rather than asserting the
// switchers are present in the topbar (they no longer are, by design).
async function openSettingsPanel() {
  fireEvent.click(screen.getByLabelText("Conversation options"));
  fireEvent.click(await screen.findByText("Settings"));
  await screen.findByLabelText("Conversation mode");
}

describe("ConversationView mode/model switcher wiring", () => {
  it("seeds the switchers from conversation.mode/model and calls setConversationMode with personaIds[0] for a DM", async () => {
    setConversationMode.mockResolvedValue(undefined);
    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "auto",
      model: "DeepSeek V3.2",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);
    await openSettingsPanel();

    expect(screen.getByLabelText("Conversation mode")).toHaveValue("auto");
    expect(screen.getByLabelText("Conversation model")).toHaveValue("DeepSeek V3.2");

    fireEvent.change(screen.getByLabelText("Conversation mode"), { target: { value: "manual" } });
    await waitFor(() => expect(setConversationMode).toHaveBeenCalledWith("dm-rex", "rex", "manual"));
  });

  it("uses personaIds[0] as the lead persona for a group conversation, not any other member", async () => {
    setConversationMode.mockResolvedValue(undefined);
    const conversation: Conversation = {
      id: "grp-auth",
      kind: "group",
      name: "#auth-rework",
      personaIds: ["ada", "rex", "vex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "Claude Opus 4.8",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[ADA, REX, VEX]} initialMessages={[]} />);
    await openSettingsPanel();

    fireEvent.change(screen.getByLabelText("Conversation mode"), { target: { value: "bypass" } });
    await waitFor(() => expect(setConversationMode).toHaveBeenCalledWith("grp-auth", "ada", "bypass"));
  });

  it("shows the conversation's effective model in the settings panel, not the persona's own static model, once they diverge", async () => {
    // Regression (adapted for the panel move): the model shown here used
    // to sometimes render `primaryPersona.model` (the persona's own
    // configured model) instead of `conversation.model` (the documented
    // effective model, lib/api.ts) -- two different answers to the same
    // question. Here Rex's own config is still "DeepSeek V3.2" but this
    // conversation has already been switched to "Claude Opus 4.8" (e.g. a
    // prior session-scoped override), so the panel must reflect the
    // conversation, not the persona.
    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "Claude Opus 4.8",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);
    await openSettingsPanel();

    expect(screen.getByLabelText("Conversation model")).toHaveValue("Claude Opus 4.8");
  });

  it("no longer shows the model in the topbar status pill at all", () => {
    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "Claude Opus 4.8",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);

    expect(screen.queryByText(/Claude Opus 4\.8/)).not.toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
  });
});

describe("ConversationView typing indicator", () => {
  it("shows a typing indicator for a persona/typing frame and hides it once done", async () => {
    let onEvent: (event: ConversationEvent) => void = () => {};
    subscribeToConversation.mockImplementation((_id, cb) => {
      onEvent = cb;
      return () => {};
    });

    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "DeepSeek V3.2",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);

    expect(screen.queryByText(/is typing/)).not.toBeInTheDocument();

    onEvent({ type: "persona/typing", payload: { persona_id: "rex" } });
    await waitFor(() => expect(screen.getByText("Rex is typing")).toBeInTheDocument());

    onEvent({ type: "persona/typing", payload: { persona_id: "rex", done: true } });
    await waitFor(() => expect(screen.queryByText(/is typing/)).not.toBeInTheDocument());
  });

  it("clears a stale typing indicator when navigating to a different conversation", async () => {
    let onEvent: (event: ConversationEvent) => void = () => {};
    subscribeToConversation.mockImplementation((_id, cb) => {
      onEvent = cb;
      return () => {};
    });

    const conversationA: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "DeepSeek V3.2",
      archived: false,
    };
    const { rerender } = render(
      <ConversationView conversation={conversationA} personas={[REX]} initialMessages={[]} />
    );
    onEvent({ type: "persona/typing", payload: { persona_id: "rex" } });
    await waitFor(() => expect(screen.getByText("Rex is typing")).toBeInTheDocument());

    const conversationB: Conversation = {
      id: "dm-ada",
      kind: "dm",
      personaIds: ["ada"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "Claude Opus 4.8",
      archived: false,
    };
    rerender(<ConversationView conversation={conversationB} personas={[ADA]} initialMessages={[]} />);

    await waitFor(() => expect(screen.queryByText(/is typing/)).not.toBeInTheDocument());
  });
});

describe("ConversationView stop button", () => {
  it("shows a Stop button only while a persona is typing, and calls stopConversation when clicked", async () => {
    stopConversation.mockResolvedValue(undefined);
    let onEvent: (event: ConversationEvent) => void = () => {};
    subscribeToConversation.mockImplementation((_id, cb) => {
      onEvent = cb;
      return () => {};
    });

    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "DeepSeek V3.2",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);

    expect(screen.queryByLabelText("Stop generating")).not.toBeInTheDocument();

    onEvent({ type: "persona/typing", payload: { persona_id: "rex" } });
    await waitFor(() => expect(screen.getByLabelText("Stop generating")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Stop generating"));
    await waitFor(() => expect(stopConversation).toHaveBeenCalledWith("dm-rex"));
  });

  it("hides the Stop button again once the backend confirms every persona stopped typing", async () => {
    let onEvent: (event: ConversationEvent) => void = () => {};
    subscribeToConversation.mockImplementation((_id, cb) => {
      onEvent = cb;
      return () => {};
    });

    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "DeepSeek V3.2",
      archived: false,
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);

    onEvent({ type: "persona/typing", payload: { persona_id: "rex" } });
    await waitFor(() => expect(screen.getByLabelText("Stop generating")).toBeInTheDocument());

    // _drive_turn's finally block broadcasts persona/typing done=true on
    // every exit path, including a stop-triggered cancellation -- so the
    // button disappearing here is what actually confirms the stop landed.
    onEvent({ type: "persona/typing", payload: { persona_id: "rex", done: true } });
    await waitFor(() => expect(screen.queryByLabelText("Stop generating")).not.toBeInTheDocument());
  });
});

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "dm-rex",
    kind: "dm",
    personaIds: ["rex"],
    updatedAt: new Date().toISOString(),
    mode: "manual",
    model: "DeepSeek V3.2",
    archived: false,
    ...overrides,
  };
}

describe("ConversationView conversation menu (archive/delete)", () => {
  it("toggles archived via the menu and reflects it back through the panel prop", async () => {
    setConversationArchived.mockResolvedValue(undefined);
    render(<ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[]} />);

    fireEvent.click(screen.getByLabelText("Conversation options"));
    fireEvent.click(await screen.findByText("Archive"));

    await waitFor(() => expect(setConversationArchived).toHaveBeenCalledWith("dm-rex", true));
    // RosterList is a separately server-fetched sibling -- without this,
    // it only ever picked up the change on the next full navigation.
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("deletes the conversation and navigates home after confirming", async () => {
    deleteConversation.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[]} />);

    fireEvent.click(screen.getByLabelText("Conversation options"));
    fireEvent.click(await screen.findByText("Delete"));

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith("dm-rex"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });

  it("does not delete when the confirm dialog is declined", async () => {
    // deleteConversation is a module-scoped vi.fn() with no auto-clear
    // between tests in this file (see the other it() above) -- reset its
    // call history explicitly so this "not called" assertion checks THIS
    // test's behavior, not whether an earlier test also called it.
    deleteConversation.mockClear();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[]} />);

    fireEvent.click(screen.getByLabelText("Conversation options"));
    fireEvent.click(await screen.findByText("Delete"));

    expect(deleteConversation).not.toHaveBeenCalled();
  });
});

describe("ConversationView conversation settings panel", () => {
  it("pre-fills the context field from conversation.context and saves an edit", async () => {
    setConversationContext.mockResolvedValue(undefined);
    const conversation = makeConversation({ context: "Keep it casual." });
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);
    await openSettingsPanel();

    const textarea = screen.getByLabelText("Ground rules / context") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Keep it casual.");

    fireEvent.change(textarea, { target: { value: "No work talk." } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(setConversationContext).toHaveBeenCalledWith("dm-rex", "No work talk."));
  });
});

describe("ConversationView message reply/edit/delete/react", () => {
  function makeMessage(overrides: Partial<import("@/lib/api").Message> = {}) {
    return {
      id: "m1",
      conversationId: "dm-rex",
      actor: "you",
      text: "hello there",
      timestamp: new Date().toISOString(),
      eventType: "user/message",
      ...overrides,
    };
  }

  it("populates the composer's reply strip when Reply is clicked, and includes replyToId on send", async () => {
    sendMessage.mockResolvedValue({ ...makeMessage({ id: "m2", text: "reply text" }) });
    const original = makeMessage();
    render(
      <ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[original]} />
    );

    fireEvent.click(screen.getByLabelText("Reply"));
    expect(screen.getByText(/Replying to You/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Message/), { target: { value: "reply text" } });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith("dm-rex", "reply text", "m1"));
  });

  it("edits your own message and shows the updated text with an (edited) tag", async () => {
    editMessage.mockResolvedValue(makeMessage({ text: "hello there, edited", edited: true }));
    const original = makeMessage();
    render(
      <ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[original]} />
    );

    fireEvent.click(screen.getByLabelText("Edit"));
    const textarea = screen.getByDisplayValue("hello there");
    fireEvent.change(textarea, { target: { value: "hello there, edited" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(editMessage).toHaveBeenCalledWith("dm-rex", "m1", "hello there, edited"));
    await waitFor(() => expect(screen.getByText("hello there, edited")).toBeInTheDocument());
    expect(screen.getByText("(edited)")).toBeInTheDocument();
  });

  it("deletes your own message after confirming and shows the redacted placeholder", async () => {
    deleteMessage.mockResolvedValue(makeMessage({ text: "", deleted: true }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const original = makeMessage();
    render(
      <ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[original]} />
    );

    fireEvent.click(screen.getByLabelText("Delete"));

    await waitFor(() => expect(deleteMessage).toHaveBeenCalledWith("dm-rex", "m1"));
    await waitFor(() => expect(screen.getByText("This message was deleted.")).toBeInTheDocument());
  });

  it("does not show Edit/Delete for a message you did not author", () => {
    const rexMessage = makeMessage({ actor: "rex", text: "hi from rex" });
    render(
      <ConversationView conversation={makeConversation()} personas={[REX]} initialMessages={[rexMessage]} />
    );

    expect(screen.queryByLabelText("Edit")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Delete")).not.toBeInTheDocument();
    // Reply and React are still offered on anyone's message.
    expect(screen.getByLabelText("Reply")).toBeInTheDocument();
    expect(screen.getByLabelText("React")).toBeInTheDocument();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Conversation, Persona } from "@/lib/api";

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

const subscribeToConversation = vi.fn(() => () => {});
const setConversationMode = vi.fn();
const setConversationModel = vi.fn();
const sendMessage = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    subscribeToConversation: (...args: unknown[]) =>
      (subscribeToConversation as unknown as (...a: unknown[]) => () => void)(...args),
    setConversationMode: (...args: unknown[]) => setConversationMode(...args),
    setConversationModel: (...args: unknown[]) => setConversationModel(...args),
    sendMessage: (...args: unknown[]) => sendMessage(...args),
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
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);

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
    };
    render(<ConversationView conversation={conversation} personas={[ADA, REX, VEX]} initialMessages={[]} />);

    fireEvent.change(screen.getByLabelText("Conversation mode"), { target: { value: "bypass" } });
    await waitFor(() => expect(setConversationMode).toHaveBeenCalledWith("grp-auth", "ada", "bypass"));
  });

  it("shows the conversation's effective model in the status pill, not the persona's own static model, once they diverge", () => {
    // Regression: the status pill used to render `primaryPersona.model`
    // (the persona's own configured model) right next to ModelSwitcher,
    // which renders `conversation.model` (the documented effective model,
    // lib/api.ts) -- two different answers to the same question. Here Rex's
    // own config is still "DeepSeek V3.2" but this conversation has already
    // been switched to "Claude Opus 4.8" (e.g. a prior session-scoped
    // override), so the pill must reflect the conversation, not the persona.
    const conversation: Conversation = {
      id: "dm-rex",
      kind: "dm",
      personaIds: ["rex"],
      updatedAt: new Date().toISOString(),
      mode: "manual",
      model: "Claude Opus 4.8",
    };
    render(<ConversationView conversation={conversation} personas={[REX]} initialMessages={[]} />);

    expect(screen.getByText(/Working · Claude Opus 4\.8/)).toBeInTheDocument();
    expect(screen.queryByText(/Working · DeepSeek V3\.2/)).not.toBeInTheDocument();
  });
});

import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ModelSwitcher } from "@/components/conversation/ModelSwitcher";

afterEach(() => cleanup());

const setConversationModel = vi.fn();
vi.mock("@/lib/api", () => ({
  setConversationModel: (...args: unknown[]) => setConversationModel(...args),
}));

beforeEach(() => {
  setConversationModel.mockReset();
});

// Real model IDs (matching lib/personaDetails.ts's MODEL_OPTIONS values) --
// NOT display labels. An earlier revision of both MODEL_OPTIONS and these
// tests used the display label itself as the <option value> (e.g.
// "Claude Opus 4.8"), which meant selecting a model actually sent that
// label string to the backend as if it were a real LiteLLM model id.

describe("ModelSwitcher", () => {
  it("renders the conversation's current model as the selected option", () => {
    render(
      <ModelSwitcher conversationId="dm-rex" personaId="rex" model="deepseek/deepseek-chat" onModelChanged={() => {}} />
    );
    expect(screen.getByLabelText("Conversation model")).toHaveValue("deepseek/deepseek-chat");
  });

  it("does not show a scope picker until a different model is picked", () => {
    render(
      <ModelSwitcher conversationId="dm-rex" personaId="rex" model="deepseek/deepseek-chat" onModelChanged={() => {}} />
    );
    expect(screen.queryByText(/apply to/i)).not.toBeInTheDocument();
  });

  it("asks for a scope and calls setConversationModel with scope 'once' for 'Just this message'", async () => {
    setConversationModel.mockResolvedValue(undefined);
    const onModelChanged = vi.fn();
    render(
      <ModelSwitcher conversationId="dm-rex" personaId="rex" model="deepseek/deepseek-chat" onModelChanged={onModelChanged} />
    );

    fireEvent.change(screen.getByLabelText("Conversation model"), { target: { value: "claude-opus-4-6" } });
    expect(screen.getByText(/apply to/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /just this message/i }));

    await waitFor(() =>
      expect(setConversationModel).toHaveBeenCalledWith("dm-rex", "rex", "claude-opus-4-6", "once")
    );
    expect(onModelChanged).toHaveBeenCalledWith("claude-opus-4-6");
  });

  it("calls setConversationModel with scope 'session' for 'This conversation'", async () => {
    setConversationModel.mockResolvedValue(undefined);
    const onModelChanged = vi.fn();
    render(
      <ModelSwitcher conversationId="dm-rex" personaId="rex" model="deepseek/deepseek-chat" onModelChanged={onModelChanged} />
    );

    fireEvent.change(screen.getByLabelText("Conversation model"), { target: { value: "gemini/gemini-3-pro" } });
    fireEvent.click(screen.getByRole("button", { name: /this conversation/i }));

    await waitFor(() =>
      expect(setConversationModel).toHaveBeenCalledWith("dm-rex", "rex", "gemini/gemini-3-pro", "session")
    );
    expect(onModelChanged).toHaveBeenCalledWith("gemini/gemini-3-pro");
  });

  it("cancel dismisses the scope picker without calling the backend", () => {
    render(
      <ModelSwitcher conversationId="dm-rex" personaId="rex" model="deepseek/deepseek-chat" onModelChanged={() => {}} />
    );
    fireEvent.change(screen.getByLabelText("Conversation model"), { target: { value: "gemini/gemini-3-pro" } });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByText(/apply to/i)).not.toBeInTheDocument();
    expect(setConversationModel).not.toHaveBeenCalled();
  });

  it("surfaces an inline error and does not call onModelChanged when the backend call fails", async () => {
    setConversationModel.mockRejectedValue(new Error("network error"));
    const onModelChanged = vi.fn();
    render(
      <ModelSwitcher conversationId="dm-rex" personaId="rex" model="deepseek/deepseek-chat" onModelChanged={onModelChanged} />
    );

    fireEvent.change(screen.getByLabelText("Conversation model"), { target: { value: "claude-opus-4-6" } });
    fireEvent.click(screen.getByRole("button", { name: /just this message/i }));

    await screen.findByText(/couldn.t change model/i);
    expect(onModelChanged).not.toHaveBeenCalled();
  });
});

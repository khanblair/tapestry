import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ModeSwitcher } from "@/components/conversation/ModeSwitcher";

afterEach(() => cleanup());

const setConversationMode = vi.fn();
vi.mock("@/lib/api", () => ({
  setConversationMode: (...args: unknown[]) => setConversationMode(...args),
}));

beforeEach(() => {
  setConversationMode.mockReset();
});

describe("ModeSwitcher", () => {
  it("renders the conversation's current mode as the selected option", () => {
    render(
      <ModeSwitcher conversationId="dm-rex" personaId="rex" mode="manual" onModeChanged={() => {}} />
    );
    expect(screen.getByLabelText("Conversation mode")).toHaveValue("manual");
  });

  it("calls setConversationMode with the conversation id, lead persona id, and new mode on selection", async () => {
    setConversationMode.mockResolvedValue(undefined);
    const onModeChanged = vi.fn();
    render(
      <ModeSwitcher conversationId="dm-rex" personaId="rex" mode="manual" onModeChanged={onModeChanged} />
    );

    fireEvent.change(screen.getByLabelText("Conversation mode"), { target: { value: "auto" } });

    await waitFor(() => expect(setConversationMode).toHaveBeenCalledWith("dm-rex", "rex", "auto"));
    expect(onModeChanged).toHaveBeenCalledWith("auto");
  });

  it("does not call the backend when the same mode is re-selected", () => {
    render(
      <ModeSwitcher conversationId="dm-rex" personaId="rex" mode="manual" onModeChanged={() => {}} />
    );
    fireEvent.change(screen.getByLabelText("Conversation mode"), { target: { value: "manual" } });
    expect(setConversationMode).not.toHaveBeenCalled();
  });

  it("surfaces an inline error and does not call onModeChanged when the backend call fails", async () => {
    setConversationMode.mockRejectedValue(new Error("network error"));
    const onModeChanged = vi.fn();
    render(
      <ModeSwitcher conversationId="dm-rex" personaId="rex" mode="manual" onModeChanged={onModeChanged} />
    );

    fireEvent.change(screen.getByLabelText("Conversation mode"), { target: { value: "bypass" } });

    await screen.findByText(/couldn.t change mode/i);
    expect(onModeChanged).not.toHaveBeenCalled();
  });
});

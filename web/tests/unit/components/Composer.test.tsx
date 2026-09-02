import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import type { Persona } from "@/lib/api";

afterEach(() => cleanup());

const sendMessage = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    sendMessage: (...args: unknown[]) => sendMessage(...args),
  };
});

const { Composer } = await import("@/components/conversation/Composer");

const REX: Persona = {
  id: "rex",
  name: "Rex",
  role: "Developer",
  model: "DeepSeek V3.2",
  status: "online",
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

describe("Composer @-mention autocomplete", () => {
  it("shows suggestions, including an 'all' option, as soon as '@' is typed in a group", () => {
    render(<Composer conversationId="grp-1" personas={[ADA, REX]} />);
    const field = screen.getByPlaceholderText("Message…");

    fireEvent.change(field, { target: { value: "hi @" } });

    expect(screen.getByText("all")).toBeInTheDocument();
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("Rex")).toBeInTheDocument();
  });

  it("filters suggestions as more of the handle is typed", () => {
    render(<Composer conversationId="grp-1" personas={[ADA, REX]} />);
    const field = screen.getByPlaceholderText("Message…");

    fireEvent.change(field, { target: { value: "hi @re" } });

    expect(screen.getByText("Rex")).toBeInTheDocument();
    expect(screen.queryByText("Ada")).not.toBeInTheDocument();
    expect(screen.queryByText("all")).not.toBeInTheDocument();
  });

  it("omits the 'all' option in a DM, where there's only one persona to mention", () => {
    render(<Composer conversationId="dm-rex" personas={[REX]} />);
    fireEvent.change(screen.getByPlaceholderText("Message…"), { target: { value: "@" } });

    expect(screen.getByText("Rex")).toBeInTheDocument();
    expect(screen.queryByText("all")).not.toBeInTheDocument();
  });

  it("inserts the selected persona's id, not display name, so the backend's handle match is unambiguous", () => {
    render(<Composer conversationId="grp-1" personas={[ADA, REX]} />);
    const field = screen.getByPlaceholderText("Message…") as HTMLTextAreaElement;

    fireEvent.change(field, { target: { value: "hi @re" } });
    // Selection fires on mousedown (not click) so the textarea never loses
    // focus/selection to the button -- see MentionAutocomplete's own note.
    fireEvent.mouseDown(screen.getByText("Rex"));

    expect(field.value).toBe("hi @rex ");
  });

  it("does not open on a mid-word '@' like an email address", () => {
    render(<Composer conversationId="grp-1" personas={[ADA, REX]} />);
    fireEvent.change(screen.getByPlaceholderText("Message…"), { target: { value: "foo@bar" } });

    expect(screen.queryByText("Rex")).not.toBeInTheDocument();
  });

  it("Enter selects the highlighted suggestion instead of sending the message", () => {
    render(<Composer conversationId="grp-1" personas={[ADA, REX]} />);
    const field = screen.getByPlaceholderText("Message…") as HTMLTextAreaElement;

    fireEvent.change(field, { target: { value: "@" } });
    fireEvent.keyDown(field, { key: "Enter" });

    expect(sendMessage).not.toHaveBeenCalled();
    expect(field.value).toBe("@all ");
  });

  it("Escape closes the menu without inserting anything", () => {
    render(<Composer conversationId="grp-1" personas={[ADA, REX]} />);
    const field = screen.getByPlaceholderText("Message…") as HTMLTextAreaElement;

    fireEvent.change(field, { target: { value: "hi @" } });
    fireEvent.keyDown(field, { key: "Escape" });

    expect(screen.queryByText("all")).not.toBeInTheDocument();
    expect(field.value).toBe("hi @");
  });
});

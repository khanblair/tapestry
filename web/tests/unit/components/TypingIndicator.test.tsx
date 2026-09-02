import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TypingIndicator } from "@/components/conversation/TypingIndicator";
import type { Persona } from "@/lib/api";

const ADA: Persona = { id: "ada", name: "Ada", role: "Architect", model: "x", status: "online", color: "#3B82F6" };
const REX: Persona = { id: "rex", name: "Rex", role: "Developer", model: "x", status: "online", color: "#8B5CF6" };

describe("TypingIndicator", () => {
  it("renders nothing for an empty personas list", () => {
    const { container } = render(<TypingIndicator personas={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows one avatar and singular phrasing for one persona", () => {
    render(<TypingIndicator personas={[ADA]} />);
    expect(screen.getByText("Ada is typing")).toBeInTheDocument();
    expect(document.querySelectorAll(".typing-avatars .avatar")).toHaveLength(1);
  });

  it("shows an avatar per persona (not just the first) for two personas typing at once", () => {
    render(<TypingIndicator personas={[ADA, REX]} />);
    expect(screen.getByText("Ada and Rex are typing")).toBeInTheDocument();
    expect(document.querySelectorAll(".typing-avatars .avatar")).toHaveLength(2);
  });
});

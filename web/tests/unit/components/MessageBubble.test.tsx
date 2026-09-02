import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "@/components/conversation/MessageBubble";
import type { Message } from "@/lib/api";

function makeMessage(text: string): Message {
  return {
    id: "m1",
    conversationId: "grp-test",
    actor: "you",
    text,
    timestamp: "10:00",
    eventType: "message",
  };
}

describe("MessageBubble markdown rendering", () => {
  it("renders bold/italic/list markdown as real elements, not literal asterisks", () => {
    render(
      <MessageBubble message={makeMessage("**bold** and *italic* and:\n\n- one\n- two")} />
    );

    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("italic").tagName).toBe("EM");
    expect(screen.getByText("one").closest("li")).not.toBeNull();
    // The literal markdown syntax must not leak through as plain text.
    expect(screen.queryByText(/\*\*bold\*\*/)).not.toBeInTheDocument();
  });

  it("highlights an @mention with a real .mention span, even inside bold text", () => {
    render(<MessageBubble message={makeMessage("hey **@rex** check this out")} />);

    const mention = screen.getByText("@rex");
    expect(mention.tagName).toBe("SPAN");
    expect(mention).toHaveClass("mention");
    // Regression guard for the bug this test was written against: the
    // custom remark node type reached mdast-util-to-hast's
    // unknownHandler and was silently dropped, so NO .mention element
    // (bold or otherwise) ever existed in the DOM -- only text().
    expect(mention.closest("strong")).not.toBeNull();
  });

  it("highlights a plain (non-bold) @mention too", () => {
    render(<MessageBubble message={makeMessage("hey @rex what do you think?")} />);

    const mention = screen.getByText("@rex");
    expect(mention.tagName).toBe("SPAN");
    expect(mention).toHaveClass("mention");
  });
});

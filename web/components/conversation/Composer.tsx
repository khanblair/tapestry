"use client";

import { useRef, useState } from "react";
import type { Message } from "@/lib/api";
import { sendMessage } from "@/lib/api";
import { SendIcon } from "@/components/ui/icons";

export interface ComposerProps {
  conversationId: string;
  /** Name shown in the placeholder, e.g. "Message Rex…" — matches the prototype's per-recipient placeholder. */
  recipientName?: string;
  /** Called with the sent message once the backend confirms it, so the caller can append it optimistically. */
  onSent?: (message: Message) => void;
}

/**
 * The message input + send button. The prototype's composer was a fake
 * `<div class="field">` with placeholder text and no real input — this is
 * a real textarea wired to lib/api's sendMessage, with Enter-to-send
 * (Shift+Enter for a newline) matching standard chat-app convention.
 */
export function Composer({ conversationId, recipientName, onSent }: ComposerProps) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    try {
      const message = await sendMessage(conversationId, trimmed);
      setText("");
      onSent?.(message);
    } catch (error) {
      console.error("Failed to send message", error);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  }

  return (
    <div className="composer">
      <textarea
        ref={textareaRef}
        className="field"
        rows={1}
        placeholder={recipientName ? `Message ${recipientName}…` : "Message…"}
        value={text}
        disabled={sending}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      <button
        type="button"
        className="send"
        aria-label="Send"
        disabled={sending || !text.trim()}
        onClick={() => void submit()}
      >
        <SendIcon size={15} />
      </button>
    </div>
  );
}

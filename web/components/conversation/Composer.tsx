"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { Message, Persona } from "@/lib/api";
import { sendMessage } from "@/lib/api";
import { SendIcon, SmileIcon, XIcon } from "@/components/ui/icons";
import {
  MentionAutocomplete,
  filterMentionCandidates,
  mentionCandidates,
  mentionInsertText,
  type MentionCandidate,
} from "./MentionAutocomplete";

// Same lazy-load rationale as MessageBubble's own reaction picker: a full
// emoji data set has no business in the initial bundle.
const EmojiPicker = dynamic(() => import("emoji-picker-react"), { ssr: false });

export interface ComposerProps {
  conversationId: string;
  /** Name shown in the placeholder, e.g. "Message Rex…" — matches the prototype's per-recipient placeholder. */
  recipientName?: string;
  /** This conversation's members, for "@"-autocomplete suggestions. Empty/omitted disables it entirely. */
  personas?: Persona[];
  /** Called with the sent message once the backend confirms it, so the caller can append it optimistically. */
  onSent?: (message: Message) => void;
  /** Set when the human tapped Reply on an earlier message — shown as a dismissible quoted strip above the input. */
  replyingTo?: { message: Message; actorName: string };
  onCancelReply?: () => void;
}

/** An active "@token" ending at the cursor, e.g. typing "hey @re" -> {start: 4, query: "re"} ("start" is the index of "@" itself). Not a match mid-word (an email address, "foo@bar") since it requires a space or start-of-text right before the "@". */
function activeMentionAt(text: string, cursor: number): { start: number; query: string } | null {
  const uptoCursor = text.slice(0, cursor);
  const match = /(?:^|\s)@(\w*)$/.exec(uptoCursor);
  if (!match) return null;
  const atIndex = uptoCursor[match.index] === "@" ? match.index : match.index + 1;
  return { start: atIndex, query: match[1] };
}

/**
 * The message input + send button. The prototype's composer was a fake
 * `<div class="field">` with placeholder text and no real input — this is
 * a real textarea wired to lib/api's sendMessage, with Enter-to-send
 * (Shift+Enter for a newline) matching standard chat-app convention.
 *
 * "@"-autocomplete: typing "@" opens a suggestion list (every conversation
 * member, plus a synthetic "all") filtered as you keep typing; arrow
 * keys/Enter/Tab pick one, Escape closes it. Purely a client-side text
 * insertion aid -- the actual `@handle` parsing/resolution already lives
 * in the backend (`api.py`'s `_resolve_mentions`), unaffected by this.
 */
export function Composer({
  conversationId,
  recipientName,
  personas,
  onSent,
  replyingTo,
  onCancelReply,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const emojiPopoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!emojiPickerOpen) return;
    function handleClick(event: MouseEvent) {
      if (emojiPopoverRef.current && !emojiPopoverRef.current.contains(event.target as Node)) {
        setEmojiPickerOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [emojiPickerOpen]);

  const candidates = useMemo(() => mentionCandidates(personas ?? []), [personas]);
  const filtered = useMemo(
    () => (mention ? filterMentionCandidates(candidates, mention.query) : []),
    [candidates, mention]
  );
  const mentionOpen = mention !== null && filtered.length > 0;

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    try {
      const message = await sendMessage(conversationId, trimmed, replyingTo?.message.id);
      setText("");
      setMention(null);
      onSent?.(message);
      onCancelReply?.();
    } catch (error) {
      console.error("Failed to send message", error);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  }

  // Inserts at the textarea's own cursor position (not just appended to
  // the end) -- the picker stays open across multiple picks, same as
  // emoji-picker-react's own default click behavior, so someone can drop
  // in a few emojis in a row without reopening it each time.
  function insertEmoji(emoji: string) {
    const el = textareaRef.current;
    const cursor = el?.selectionStart ?? text.length;
    const before = text.slice(0, cursor);
    const after = text.slice(cursor);
    setText(`${before}${emoji}${after}`);
    requestAnimationFrame(() => {
      const pos = cursor + emoji.length;
      el?.focus();
      el?.setSelectionRange(pos, pos);
    });
  }

  function selectMention(candidate: MentionCandidate) {
    if (!mention) return;
    const cursor = mention.start + 1 + mention.query.length;
    const before = text.slice(0, mention.start);
    const after = text.slice(cursor);
    const insert = mentionInsertText(candidate);
    setText(`${before}@${insert} ${after}`);
    setMention(null);
    setActiveIndex(0);
    requestAnimationFrame(() => {
      const pos = before.length + insert.length + 2; // "@" + insert + trailing space
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(pos, pos);
    });
  }

  return (
    <>
      {replyingTo && (
        <div className="reply-preview-strip">
          <div className="body">
            <div className="who">Replying to {replyingTo.actorName}</div>
            <div className="snippet">
              {replyingTo.message.deleted
                ? "This message was deleted."
                : replyingTo.message.text.replace(/\s+/g, " ")}
            </div>
          </div>
          <button type="button" className="icon-btn" aria-label="Cancel reply" onClick={onCancelReply}>
            <XIcon size={14} />
          </button>
        </div>
      )}
      <div className="composer">
      {mentionOpen && (
        <MentionAutocomplete
          candidates={filtered}
          activeIndex={activeIndex}
          onSelect={selectMention}
          onHover={setActiveIndex}
        />
      )}
      <div className="composer-emoji-anchor" ref={emojiPopoverRef}>
        {emojiPickerOpen && (
          <div className="composer-emoji-popover">
            <EmojiPicker onEmojiClick={(data: { emoji: string }) => insertEmoji(data.emoji)} />
          </div>
        )}
        <button
          type="button"
          className="icon-btn composer-emoji-btn"
          aria-label="Add emoji"
          disabled={sending}
          onClick={() => setEmojiPickerOpen((open) => !open)}
        >
          <SmileIcon size={18} />
        </button>
      </div>
      <textarea
        ref={textareaRef}
        className="field"
        rows={1}
        placeholder={recipientName ? `Message ${recipientName}…` : "Message…"}
        value={text}
        disabled={sending}
        onChange={(event) => {
          const value = event.target.value;
          setText(value);
          const cursor = event.target.selectionStart ?? value.length;
          setMention(activeMentionAt(value, cursor));
          setActiveIndex(0);
        }}
        onKeyDown={(event) => {
          if (mentionOpen) {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActiveIndex((index) => (index + 1) % filtered.length);
              return;
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setActiveIndex((index) => (index - 1 + filtered.length) % filtered.length);
              return;
            }
            if (event.key === "Enter" || event.key === "Tab") {
              event.preventDefault();
              selectMention(filtered[activeIndex]);
              return;
            }
            if (event.key === "Escape") {
              event.preventDefault();
              setMention(null);
              return;
            }
          }
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
    </>
  );
}
